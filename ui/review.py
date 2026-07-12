"""Review feature — three NiceGUI pages.

1. ``/review/{project_id}``      — translator-side review management.
2. ``/review/ext/{review_id}``    — external reviewer bilingual pane.
3. ``/review/merge/{review_id}``  — side-by-side merge view.

All pages use the same high-level NiceGUI API patterns as ``ui/workspace.py``
and ``ui/segment_editor.py``: ``ui.card``, ``ui.row``, ``ui.column``,
``ui.scroll_area``, ``ui.dialog``, ``background_tasks.create``,
``state.client`` context, and the shared CSS / dark-mode helpers from
``ui.settings``.
"""
from __future__ import annotations

import asyncio
import difflib
import html as html_lib
import json
import logging
from datetime import datetime
from typing import Any

from nicegui import background_tasks, ui

from translate_core import review_manager as rm
from ui.components import busy_overlay

log = logging.getLogger(__name__)


# ======================================================================
# Shared helpers
# ======================================================================
def _fmt_remaining(expires_at: str) -> str:
    """Human-readable countdown to the funnel expiry."""
    if not expires_at:
        return "no expiry set"
    try:
        exp = datetime.fromisoformat(expires_at)
        delta = exp - datetime.now()
        if delta.total_seconds() <= 0:
            return "expired"
        hours = int(delta.total_seconds() // 3600)
        if hours >= 24:
            days = hours // 24
            return f"{days} day{'s' if days != 1 else ''} remaining"
        return f"{hours}h remaining"
    except Exception:
        return expires_at


def _is_expired(expires_at: str) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now()
    except Exception:
        return True


def _resources() -> dict[str, Any] | None:
    """Load shared app_state resources, mirroring ui/workspace.py:44-77."""
    import app_state

    tm = app_state.tm
    glossary = app_state.glossary
    kg = app_state.kg
    qa_engine = app_state.qa_engine
    parse_lang_pair = app_state.parse_lang_pair
    config = app_state.config
    load_project = app_state.load_project
    save_project = app_state.save_project

    needed = (tm, glossary, kg, qa_engine, parse_lang_pair, config, load_project, save_project)
    if any(x is None for x in needed):
        return None
    return {
        "tm": tm,
        "glossary": glossary,
        "kg": kg,
        "qa_engine": qa_engine,
        "parse_lang_pair": parse_lang_pair,
        "config": config,
        "load_project": load_project,
        "save_project": save_project,
    }


def _install_chrome() -> tuple | None:
    """Install shared CSS, dark mode, and predictions runtime.

    Returns ``(dm, page_client)`` or ``None`` if backend is not ready.
    """
    from ui import settings as ui_settings
    from ui import predictions

    res = _resources()
    if res is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.icon("hourglass_empty", size="48px").props("color=primary")
            ui.label("Backend still initializing — please reload in a moment.").classes(
                "text-lg mt-2"
            )
        return None

    import app_state as _ast
    if _ast.apply_colors is not None:
        _ast.apply_colors()
    ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")
    predictions.inject_runtime()
    dm = ui_settings.install_dark_mode()
    page_client = ui.context.client
    return dm, page_client, res


# ======================================================================
# Page 1 — Translator-side review management
# ======================================================================
@ui.page("/review/{project_id}")
def page_review(project_id: str):
    """List reviews for a project, create new reviews, manage funnel."""

    chrome = _install_chrome()
    if chrome is None:
        return
    dm, page_client, res = chrome

    data = res["load_project"](project_id)
    if data is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.label("Project not found.").classes("text-red-500 text-lg")
            ui.button("← Back to projects", on_click=lambda: ui.navigate.to("/")).classes("mt-4")
        return

    filename = data.get("filename", project_id)

    # ------------------------------------------------------------------
    # Top bar
    # ------------------------------------------------------------------
    with ui.card().classes(
        "w-full flex-row items-center justify-between rounded-none px-5 py-3 shrink-0"
    ).props("flat bordered"):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense"
            ).tooltip("Back to projects")
            with ui.column().classes("gap-0"):
                ui.label(filename).classes("text-sm font-bold leading-none")
                ui.label("Review management").classes("text-[10px] opacity-60")
            from ui import settings as _st
            _st.dark_toggle_button(dm)

        with ui.row().classes("gap-2 items-center"):
            ui.button(
                icon="translate",
                on_click=lambda: ui.navigate.to(f"/translate/{project_id}"),
            ).props("flat round dense color=grey-6").tooltip("Open in editor")
            ui.button(
                "New Review",
                icon="rate_review",
                on_click=lambda: _open_new_review_dialog(data, reviews_container, page_client),
            ).props("unelevated rounded color=positive").classes("text-[11px] font-bold")

    # ------------------------------------------------------------------
    # Reviews list
    # ------------------------------------------------------------------
    with ui.column().classes("w-full max-w-4xl mx-auto px-4 py-4 gap-4"):
        ui.label("ACTIVE REVIEWS").classes(
            "text-[10px] font-black uppercase tracking-[0.3em] opacity-60 px-2 mb-1"
        )
        reviews_container = ui.column().classes("w-full gap-3")
        _render_reviews(reviews_container, project_id, page_client)


def _render_reviews(container: ui.column, project_id: str, client) -> None:
    """Render the list of review cards."""
    container.clear()
    reviews = rm.list_reviews(original_project_id=project_id)
    if not reviews:
        with container:
            with ui.column().classes("w-full items-center py-12 opacity-30"):
                ui.icon("inbox", size="48px")
                ui.label("No reviews yet").classes("text-base font-bold")
                ui.label("Click 'New Review' to create one").classes("text-xs")
        return

    with container:
        for r in reviews:
            _render_review_card(r, container, project_id, client)


def _render_review_card(
    r: dict, container: ui.column, project_id: str, client
) -> None:
    """Render a single review card with context-appropriate buttons."""
    review_id = r["review_id"]
    n_segs = len(r.get("segments", []))
    status = r.get("status", "open")
    funnel_active = r.get("funnel_active", False)
    expires = r.get("funnel_expires_at", "")
    expired = _is_expired(expires) if expires else False
    round_trip = r.get("round_trip_count", 0)

    # Determine card state
    if status == rm.STATUS_CLOSED:
        status_color = "grey-6"
        status_text = "closed"
    elif status == rm.STATUS_MERGED:
        status_color = "positive"
        status_text = f"merged (round {round_trip + 1})"
    elif expired or not funnel_active:
        status_color = "amber-8"
        status_text = "expired"
    else:
        status_color = "info"
        status_text = status

    with ui.card().props("flat bordered").classes(
        "w-full p-4 rounded-2xl flex flex-col gap-3"
    ):
        # Header row
        with ui.row().classes("w-full justify-between items-center"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("rate_review", size="20px").props(f"color={status_color}")
                with ui.column().classes("gap-0"):
                    ui.label(f"{review_id} · {n_segs} segments").classes(
                        "text-sm font-bold"
                    )
                    ui.label(status_text).classes(
                        f"text-[10px] font-black uppercase tracking-widest text-{status_color}"
                    )
            if round_trip > 0:
                ui.badge(f"round {round_trip + 1}", color="primary").classes(
                    "text-[9px]"
                )

        # Funnel info
        if r.get("funnel_url"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon("link", size="14px").props("color=grey-6")
                url_text = r["funnel_url"] + f"/review/ext/{review_id}"
                ui.label(url_text).classes(
                    "text-xs font-mono opacity-70 flex-1 min-w-0 truncate"
                )
                ui.button(
                    icon="content_copy",
                    on_click=lambda _, t=url_text: ui.run_javascript(
                        f"navigator.clipboard.writeText({json.dumps(t)})"
                    ),
                ).props("flat round dense size=sm color=grey-6").tooltip("Copy link")
            if funnel_active and not expired:
                ui.label(f"Valid until: {expires[:16].replace('T', ' ')} ({_fmt_remaining(expires)})").classes(
                    "text-[10px] font-medium opacity-60 pl-6"
                )
            elif expired:
                ui.label(f"Expired: {expires[:16].replace('T', ' ')}").classes(
                    "text-[10px] font-medium text-amber-500 pl-6"
                )

        # Action buttons row
        with ui.row().classes("w-full items-center gap-2 pt-1"):
            # Merge view — always available when there are segments
            ui.button(
                "Merge view",
                icon="difference",
                on_click=lambda _, rid=review_id: ui.navigate.to(f"/review/merge/{rid}"),
            ).props("flat dense color=primary").classes("text-[11px]")

            if funnel_active and not expired:
                # Active funnel: prolong + stop
                ui.button(
                    "Prolong",
                    icon="schedule",
                    on_click=lambda _, r=r, c=container: _prolong_dialog(r, c, project_id, client),
                ).props("flat dense color=warning").classes("text-[11px]")
                ui.button(
                    "Stop funnel",
                    icon="close",
                    on_click=lambda _, r=r, c=container: _stop_funnel(r, c, project_id, client),
                ).props("flat dense color=negative").classes("text-[11px]")
            elif expired and status not in (rm.STATUS_MERGED, rm.STATUS_CLOSED):
                # Expired but not merged: prolong & restart
                ui.button(
                    "Prolong & restart",
                    icon="restart_alt",
                    on_click=lambda _, r=r, c=container: _restart_funnel(r, c, project_id, client),
                ).props("flat dense color=warning").classes("text-[11px]")

            if status == rm.STATUS_MERGED:
                # Merged: allow reopening for another reviewer round
                ui.button(
                    "Reopen for reviewer",
                    icon="replay",
                    on_click=lambda _, r=r, c=container: _reopen_review(r, c, project_id, client),
                ).props("flat dense color=primary").classes("text-[11px]")

            ui.button(
                icon="delete",
                on_click=lambda _, rid=review_id, c=container: _delete_review(rid, c, project_id, client),
            ).props("flat round dense size=sm color=grey-5").classes("ml-auto").tooltip("Delete review")


# --- Dialog actions --------------------------------------------------

def _open_new_review_dialog(data: dict, container: ui.column, client) -> None:
    """New Review dialog with segment selection + funnel validity."""
    segs = data.get("segments", [])
    done_segs = [s for s in segs if s.get("status") == "done"]

    with ui.dialog() as dialog, ui.card().classes("min-w-[520px] max-w-[90vw]"):
        ui.label("New Review").classes("text-h6 mb-2")

        # Segment selection mode
        ui.label("Segment selection").classes("text-caption font-bold mt-2")
        mode = ui.toggle(
            {
                "all_done": f"All completed ({len(done_segs)})",
                "range": "Range",
                "cherry": "Cherry-pick",
            },
            value="all_done",
        ).classes("w-full")

        # Range inputs
        range_row = ui.row().classes("w-full items-center gap-2")
        with range_row:
            ui.label("From #").classes("text-xs")
            range_from = ui.number(value=0, min=0, max=len(segs) - 1).props("outlined dense").classes("w-20")
            ui.label("to #").classes("text-xs")
            range_to = ui.number(value=len(segs) - 1, min=0, max=len(segs) - 1).props("outlined dense").classes("w-20")
        range_row.set_visibility(False)

        # Cherry-pick table
        cherry_container = ui.column().classes("w-full")
        cherry_container.set_visibility(False)
        with cherry_container:
            cherry_table = ui.table(
                columns=[
                    {"name": "sel", "label": "", "field": "sel", "align": "center"},
                    {"name": "n", "label": "#", "field": "n", "align": "right", "classes": "text-[10px] font-black opacity-50"},
                    {"name": "preview", "label": "Segment", "field": "preview", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "right"},
                ],
                rows=[
                    {
                        "id": s["id"],
                        "sel": s.get("status") == "done",
                        "n": s["id"] + 1,
                        "preview": (s.get("target") or s.get("source", ""))[:100],
                        "status": s.get("status", "pending"),
                    }
                    for s in segs
                ],
                row_key="id",
            ).props("virtual-scroll dense flat bordered hide-bottom").classes(
                "w-full h-[300px]"
            )

        selected_count_label = ui.label(f"{len(done_segs)} segments selected").classes(
            "text-xs font-medium opacity-70"
        )

        def _on_mode_change(e):
            range_row.set_visibility(e.value == "range")
            cherry_container.set_visibility(e.value == "cherry")
            if e.value == "all_done":
                selected_count_label.set_text(f"{len(done_segs)} segments selected")
            elif e.value == "range":
                _update_range_count()
            elif e.value == "cherry":
                _update_cherry_count()

        def _update_range_count():
            f = int(range_from.value or 0)
            t = int(range_to.value or 0)
            count = max(0, t - f + 1)
            selected_count_label.set_text(f"{count} segments selected")

        def _update_cherry_count():
            count = sum(1 for r in cherry_table.rows if r.get("sel"))
            selected_count_label.set_text(f"{count} segments selected")

        mode.on_value_change(_on_mode_change)
        range_from.on_value_change(lambda _: _update_range_count())
        range_to.on_value_change(lambda _: _update_range_count())

        # Reviewer name
        reviewer_name = ui.input("Reviewer name (optional)").props("outlined dense").classes("w-full mt-2")

        # Funnel validity
        with ui.row().classes("w-full items-center gap-2 mt-2"):
            ui.label("Funnel validity:").classes("text-xs font-bold")
            validity = ui.number(value=rm.DEFAULT_VALIDITY_DAYS, min=1, max=30).props(
                "outlined dense"
            ).classes("w-20")
            ui.label("days").classes("text-xs opacity-60")

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=lambda: dialog.close()).props("flat")

            async def _create():
                # Gather selected segment IDs
                if mode.value == "all_done":
                    selected_ids = [s["id"] for s in done_segs]
                elif mode.value == "range":
                    f = int(range_from.value or 0)
                    t = int(range_to.value or 0)
                    selected_ids = list(range(f, t + 1))
                else:
                    selected_ids = [r["id"] for r in cherry_table.rows if r.get("sel")]

                if not selected_ids:
                    ui.notify("Select at least one segment", type="warning")
                    return

                validity_days = int(validity.value or rm.DEFAULT_VALIDITY_DAYS)

                ui.notify("Creating review clone…")
                clone = rm.create_review_clone(
                    data,
                    segment_ids=selected_ids,
                    reviewer_name=reviewer_name.value or "",
                    validity_days=validity_days,
                )
                rm.save_review(clone)

                # Start funnel
                try:
                    async with busy_overlay("Starting Tailscale funnel…"):
                        url, expires = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda: rm.start_funnel(8080, validity_days),
                        )
                    clone["funnel_url"] = url
                    clone["funnel_expires_at"] = expires
                    clone["funnel_active"] = True
                    rm.save_review(clone)
                    full_url = f"{url}/review/ext/{clone['review_id']}"
                    ui.notify(f"Review created. Funnel: {full_url}", type="positive", timeout=5000)
                except Exception as e:
                    log.warning("funnel start: %s", e)
                    ui.notify(
                        f"Clone created but funnel failed: {e}",
                        type="warning",
                        timeout=5000,
                    )

                dialog.close()
                pid = data.get("project_id") or data.get("id") or ""
                _render_reviews(container, pid, client)

            ui.button("Create & Start Funnel", icon="rate_review", on_click=_create).props(
                "unelevated color=positive"
            )

    dialog.open()




def _prolong_dialog(r: dict, container: ui.column, project_id: str, client) -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px]"):
        ui.label("Prolong funnel").classes("text-h6")
        ui.label(f"Current expiry: {r.get('funnel_expires_at', '?')[:16].replace('T', ' ')}").classes(
            "text-xs opacity-70"
        )
        with ui.row().classes("items-center gap-2 mt-2"):
            ui.label("Extend by")
            extra = ui.number(value=3, min=1, max=30).props("outlined dense").classes("w-20")
            ui.label("days")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=lambda: dialog.close()).props("flat")
            ui.button("Prolong", on_click=lambda: _do_prolong(r, int(extra.value or 1), dialog, container, project_id, client)).props(
                "unelevated color=warning"
            )
    dialog.open()


def _do_prolong(r: dict, extra_days: int, dialog, container, project_id, client) -> None:
    new_expiry = rm.prolong_funnel(r, extra_days)
    rm.save_review(r)
    dialog.close()
    ui.notify(f"Funnel prolonged until {new_expiry[:16]}", type="positive")
    _render_reviews(container, project_id, client)


def _stop_funnel(r: dict, container, project_id, client) -> None:
    rm.stop_funnel()
    r["funnel_active"] = False
    rm.save_review(r)
    ui.notify("Funnel stopped", type="warning")
    _render_reviews(container, project_id, client)


def _restart_funnel(r: dict, container, project_id, client) -> None:
    validity = r.get("funnel_validity_days", rm.DEFAULT_VALIDITY_DAYS)

    async def _do():
        try:
            async with busy_overlay("Restarting funnel…"):
                url, expires = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: rm.start_funnel(8080, validity)
                )
            r["funnel_url"] = url
            r["funnel_expires_at"] = expires
            r["funnel_active"] = True
            rm.save_review(r)
            ui.notify("Funnel restarted", type="positive")
        except Exception as e:
            ui.notify(f"Failed: {e}", type="negative")
        _render_reviews(container, project_id, client)

    background_tasks.create(_do(), name="restart_funnel")


def _reopen_review(r: dict, container, project_id, client) -> None:
    validity = r.get("funnel_validity_days", rm.DEFAULT_VALIDITY_DAYS)

    async def _do():
        try:
            async with busy_overlay("Reopening review…"):
                url = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: rm.reopen_review(r, validity)
                )
            ui.notify(f"Review reopened. Funnel: {url}", type="positive", timeout=5000)
        except Exception as e:
            ui.notify(f"Failed: {e}", type="negative")
        _render_reviews(container, project_id, client)

    background_tasks.create(_do(), name="reopen_review")


def _delete_review(review_id: str, container, project_id, client) -> None:
    from ui.components import confirm_dialog

    async def _do():
        if await confirm_dialog(
            f"Delete review {review_id}? This cannot be undone.",
            confirm_label="Delete",
        ):
            rm.delete_review(review_id)
            ui.notify("Review deleted", type="warning")
            _render_reviews(container, project_id, client)

    background_tasks.create(_do(), name="delete_review")


# ======================================================================
# Page 2 — External reviewer pane
# ======================================================================
@ui.page("/review/ext/{review_id}")
def page_review_ext(review_id: str):
    """External reviewer bilingual pane — 20+ segments, readable flow."""
    chrome = _install_chrome()
    if chrome is None:
        return
    dm, page_client, res = chrome

    clone = rm.load_review(review_id)
    if clone is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.icon("link_off", size="48px").props("color=negative")
            ui.label("Review not found or link is invalid.").classes("text-lg text-red-500 mt-2")
        return

    # Check funnel status
    if not clone.get("funnel_active", False) or _is_expired(clone.get("funnel_expires_at", "")):
        with ui.column().classes("w-full h-screen items-center justify-center gap-4"):
            ui.icon("schedule", size="48px").props("color=warning")
            ui.label("This review link has expired.").classes("text-lg text-amber-500")
            ui.label("Please ask the translator to prolong the link.").classes("text-sm opacity-60")
        return

    # Load the clone into a WorkspaceState-compatible dict.
    ws_dict = dict(clone)
    ws_dict["project_id"] = review_id  # so WorkspaceState is happy
    ws_dict["filename"] = clone.get("original_filename", "Review")

    # Custom save callback — WorkspaceState autosaves its segment list
    # (with "target" as the edited field). In the reviewer page, "target"
    # is read-only — the reviewer edits "reviewer_target" via the
    # suggestion textarea. So we must NOT replace clone["segments"] with
    # WorkspaceState's copy, which would clobber the original translations
    # via set_active's current["target"] writeback. Only sync active_index.
    def _save_review_ws(ws: dict) -> None:
        clone["active_index"] = ws.get("active_index", 0)
        rm.save_review(clone)

    from ui.state import WorkspaceState

    state = WorkspaceState(ws_dict, save_callback=_save_review_ws, client=page_client)

    deps = {
        "tm": res["tm"],
        "glossary": res["glossary"],
        "kg": res["kg"],
        "qa_engine": res["qa_engine"],
        "parse_lang_pair": res["parse_lang_pair"],
    }

    # ------------------------------------------------------------------
    # Top bar — compact, reviewer-focused
    # ------------------------------------------------------------------
    with ui.card().classes(
        "w-full flex-row items-center justify-between rounded-none px-5 py-2 shrink-0"
    ).props("flat bordered"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("rate_review", size="20px").props("color=primary")
            with ui.column().classes("gap-0"):
                ui.label(clone.get("original_filename", "Review")).classes(
                    "text-sm font-bold leading-none"
                )
                ui.label(f"Review · {len(clone['segments'])} segments").classes(
                    "text-[10px] opacity-60"
                )

        with ui.row().classes("gap-2 items-center"):
            # Reviewer name input
            reviewer_input = ui.input(
                placeholder="Your name",
                value=clone.get("reviewer_name", ""),
            ).props("outlined dense").classes("w-40 text-xs")
            ui.button(icon="save", on_click=lambda: _save_reviewer_name(clone, reviewer_input)).props(
                "flat round dense color=positive"
            ).tooltip("Save your name")
            from ui import settings as ui_settings
            ui_settings.dark_toggle_button(dm)

    # ------------------------------------------------------------------
    # Main content — Glossary + KG intel section, then segment list.
    # Built inline (not via intel_panel.build) so TM/concordance are
    # never created — no hidden cards, no wasted refresh tasks. The
    # rendering patterns (card, label, container, run.io_bound refresh,
    # background_tasks.create_lazy, state.subscribe) are identical to
    # intel_panel.build — just without the TM section.
    # No right drawer: Glossary + KG live in the main column so the
    # reviewer can never get stuck without them.
    # ------------------------------------------------------------------
    from ui.intel_panel import _kg_query, _render_hit_fn, _truncate
    from nicegui import run

    glossary = deps["glossary"]
    kg = deps["kg"]
    parse_lang_pair = deps["parse_lang_pair"]

    with ui.column().classes("w-full items-center gap-0"):
        with ui.column().classes("w-full max-w-4xl px-4 pt-2 pb-2 gap-2"):
            # ---------------------------------------------------- Glossary
            with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.icon("menu_book", size="16px").props("color=primary")
                    ui.label("GLOSSARY").classes(
                        "text-[10px] font-black tracking-[.3em] opacity-70"
                    )
                gl_container = ui.column().classes("w-full gap-2")

            # ---------------------------------------------------- KG
            with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.icon("account_tree", size="16px").props("color=primary")
                    ui.label("KNOWLEDGE GRAPH").classes(
                        "text-[10px] font-black tracking-[.3em] opacity-70"
                    )
                kg_container = ui.column().classes("w-full gap-2")

            # ----------------------------------------------------------------
            # Insert helper — routes through the predictor's JS runtime
            # so click-to-insert targets the focused suggestion textarea.
            # Same pattern as intel_panel._insert.
            # ----------------------------------------------------------------
            def _insert(text: str) -> None:
                if not text:
                    return
                try:
                    (page_client or ui).run_javascript(
                        f"window.__sl_predictor && window.__sl_predictor.insertAtCursor({json.dumps(text)});"
                    )
                except Exception as ex:
                    print(f"[review insert] {ex}")

            # ----------------------------------------------------------------
            # KG refresh — exact same logic as intel_panel._refresh_kg
            # ----------------------------------------------------------------
            async def _refresh_kg():
                if not state.segments or kg is None:
                    return
                idx = state.active_index
                seg = state.segments[idx]
                src_lang, tgt_lang = parse_lang_pair(state.lang_pair)
                try:
                    hits = await run.io_bound(
                        _kg_query, seg["source"], src_lang, tgt_lang, kg,
                    )
                except Exception as e:
                    print(f"[review KG] {e}")
                    return
                if state.active_index != idx or kg_container.is_deleted:
                    return
                kg_container.clear()
                with kg_container:
                    if not hits:
                        ui.label("No KG matches for this segment.").classes(
                            "text-xs italic opacity-90"
                        )
                        return
                    by_concept: dict[str, list[dict]] = {}
                    order: list[str] = []
                    concept_by_id: dict[str, dict] = {}
                    orphans: list[dict] = []
                    for h in hits:
                        c = h.get("concept")
                        if c and c.get("id"):
                            cid = c["id"]
                            if cid not in by_concept:
                                by_concept[cid] = []
                                order.append(cid)
                                concept_by_id[cid] = c
                            by_concept[cid].append(h)
                        else:
                            orphans.append(h)
                    groups: list[tuple[dict | None, list[dict]]] = [
                        (concept_by_id[cid], by_concept[cid]) for cid in order
                    ]
                    if orphans:
                        groups.append((None, orphans))
                    for concept, group_hits in groups:
                        with ui.card().props("flat bordered").classes(
                            "w-full p-3 rounded-2xl"
                        ):
                            with ui.row().classes(
                                "w-full items-center gap-x-2 gap-y-1 mb-1 flex-wrap"
                            ):
                                ui.icon("hub", size="13px").props("color=primary")
                                if concept is None:
                                    ui.label("TERMS").classes(
                                        "text-[10px] font-black tracking-[.3em] opacity-90"
                                    )
                                else:
                                    ui.label(
                                        str(concept.get("label") or "").upper()
                                    ).classes(
                                        "text-[10px] font-black tracking-[.3em] opacity-90"
                                    )
                                    if concept.get("domain"):
                                        ui.badge(
                                            concept["domain"], color="grey-5",
                                        ).classes("text-[9px] px-1")
                                    for th in concept.get("theorists", []):
                                        ui.badge(th, color="purple-4").props(
                                            "outline"
                                        ).classes("text-[9px] px-1").tooltip(
                                            "thinker who uses this concept"
                                        )
                                    for ln in concept.get("lineages", []):
                                        if ln not in concept.get("theorists", []):
                                            ui.badge(ln, color="grey-5").props(
                                                "outline"
                                            ).classes("text-[9px] px-1").tooltip("lineage")
                                    for ct in concept.get("containers", []):
                                        ui.badge(ct, color="teal-5").props(
                                            "outline"
                                        ).classes("text-[9px] px-1").tooltip(
                                            "you translated this term in this work"
                                        )
                            with ui.column().classes("w-full gap-1"):
                                for h in group_hits:
                                    _render_hit_fn(h, _insert)

            # ----------------------------------------------------------------
            # Glossary refresh — exact same logic as intel_panel._refresh_gl
            # ----------------------------------------------------------------
            async def _refresh_gl():
                if not state.segments or glossary is None:
                    return
                idx = state.active_index
                seg = state.segments[idx]
                src, tgt = parse_lang_pair(state.lang_pair)
                try:
                    hits = await run.io_bound(
                        glossary.lookup_terms, seg["source"], src, tgt,
                    ) or []
                except Exception as e:
                    print(f"[review glossary] {e}")
                    return
                if state.active_index != idx or gl_container.is_deleted:
                    return
                gl_container.clear()
                with gl_container:
                    if not hits:
                        ui.label("No glossary terms in this segment.").classes(
                            "text-xs italic opacity-60"
                        )
                        return
                    with ui.row().classes("w-full gap-2 items-center flex-wrap"):
                        for g in hits:
                            s_term = g.get("source_term", "")
                            t_term = g.get("target_term", "")
                            chip_label = f"{_truncate(s_term, 30)} → {_truncate(t_term, 30)}" if t_term else _truncate(s_term, 30)
                            ui.button(
                                chip_label,
                                on_click=lambda _e, t=t_term: _insert(t),
                            ).props("unelevated rounded dense color=positive").classes(
                                "text-[11px] font-bold px-3 h-8 normal-case"
                            ).tooltip(
                                g.get("note") or "Click to insert"
                            )

            # ----------------------------------------------------------------
            # Refresh Glossary + KG on segment change — same pattern as
            # intel_panel._refresh_all but without the TM refresh.
            # ----------------------------------------------------------------
            def _refresh_all():
                sid = id(state)
                background_tasks.create_lazy(_refresh_kg(), name=f"review_kg_refresh_{sid}")
                background_tasks.create_lazy(_refresh_gl(), name=f"review_gl_refresh_{sid}")

            state.subscribe("active_index", _refresh_all)
            _refresh_all()

        # Segment list below the intel panel
        with ui.column().classes("w-full max-w-4xl px-4 py-2 gap-0"):
            ui.label("Review segments — suggest changes in the edit field, leave comments if needed. Use 'Copy' to start from the current translation.").classes(
                "text-[10px] opacity-50 px-2 pb-2"
            )
            _build_review_segment_list(clone, state, deps, page_client)

        # ── Review completed button ──
        # When the reviewer is truly done, they click this to signal
        # the translator. Sets reviewer_completed=True on the clone.
        _completed = clone.get("reviewer_completed", False)

        with ui.column().classes("w-full max-w-4xl px-4 pb-6 gap-2"):
            if _completed:
                with ui.row().classes("w-full items-center justify-center gap-2 py-4"):
                    ui.icon("check_circle", size="24px").props("color=positive")
                    ui.label("Review completed — the translator has been notified.").classes(
                        "text-sm font-bold text-positive"
                    )
            else:
                with ui.row().classes("w-full items-center justify-center gap-3 py-4"):
                    def _on_complete(_=None, c=clone):
                        c["reviewer_completed"] = True
                        c["reviewer_completed_at"] = datetime.now().isoformat(timespec="seconds")
                        rm.save_review(c)
                        ui.notify("Review completed — translator notified.", type="positive")
                        # Re-render the page to show the completed state.
                        ui.navigate.to(f"/review/ext/{c['review_id']}")

                    ui.button(
                        "Review completed",
                        icon="task_alt",
                        on_click=_on_complete,
                    ).props("unelevated rounded color=positive size=lg").classes(
                        "px-8 py-3 font-black tracking-[.2em] text-[12px]"
                    ).tooltip("Click when you are done reviewing all segments")


def _save_reviewer_name(clone: dict, input_el) -> None:
    clone["reviewer_name"] = input_el.value or ""
    rm.save_review(clone)
    ui.notify("Name saved", type="positive", timeout=1000)


def _build_review_segment_list(
    clone: dict, state, deps: dict, page_client
) -> None:
    """Build the scrollable list of fully-expanded review segment cards.

    Every segment is shown in full — no collapse/expand. When the
    reviewer focuses a suggestion textarea, state.set_active(idx) fires
    so the intel panel (Glossary + KG) populates for that segment, and
    predictions.push_bundle seeds the ghost-text predictor for that
    textarea — exactly the same pattern as the main editor's
    _on_active_change.
    """
    scroll = ui.scroll_area().classes("w-full h-[80vh]")
    with scroll:
        with ui.column().classes("w-full gap-1"):
            for i, seg in enumerate(clone["segments"]):
                _build_segment_card(seg, i, clone, state, deps, page_client)


def _build_segment_card(
    seg: dict, idx: int, clone: dict, state, deps: dict, page_client
) -> None:
    """Build one fully-expanded review segment card.

    No collapse/expand toggle — every segment is shown in full:
    source, current translation, suggested edit textarea (with Copy
    button), and comment — so the reviewer sees a continued readable
    bilingual flow.
    """
    from ui import predictions

    tm = deps["tm"]
    glossary = deps["glossary"]
    kg = deps["kg"]
    parse_lang_pair = deps["parse_lang_pair"]

    _oid = seg.get("original_id")
    orig_id: int = _oid if _oid is not None else seg.get("id", idx)
    source = seg.get("source", "")
    target = seg.get("target", "")
    reviewer_target = seg.get("reviewer_target", "")
    reviewer_comment = seg.get("reviewer_comment", "")
    reviewer_status = seg.get("reviewer_status", "pending")

    # Status badge
    status_map = {
        "pending": ("grey-6", "○"),
        "suggested": ("warning", "✎"),
        "commented": ("info", "💬"),
        "approved": ("positive", "✓"),
    }
    badge_color, badge_icon = status_map.get(reviewer_status, ("grey-6", "○"))

    with ui.card().props("flat bordered").classes(
        "w-full p-0 rounded-xl overflow-hidden shrink-0"
    ):
        # Header row — segment number + status badge
        with ui.row().classes(
            "w-full px-3 py-1.5 items-center gap-2 border-b border-gray-200 dark:border-gray-700"
        ):
            ui.icon(badge_icon, size="14px").props(f"color={badge_color}")
            ui.label(f"#{orig_id + 1}").classes(
                "text-[10px] font-black tabular-nums opacity-50 shrink-0"
            )

        # Source (read-only, full text)
        with ui.column().classes("w-full px-3 pt-2 pb-1 gap-1"):
            ui.label("SOURCE").classes(
                "text-[9px] font-black tracking-[0.2em] uppercase opacity-50"
            )
            ui.label(source).classes(
                "leading-relaxed text-sm w-full"
            ).style(
                'font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif; '
                "font-size: 16px; line-height: 1.625;"
            )

        # Current translation (read-only, full text)
        with ui.column().classes("w-full px-3 pt-1 pb-1 gap-1"):
            ui.label("CURRENT TRANSLATION").classes(
                "text-[9px] font-black tracking-[0.2em] uppercase opacity-50"
            )
            ui.label(target).classes(
                "leading-relaxed text-sm w-full"
            ).style(
                'font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif; '
                "font-size: 16px; line-height: 1.625;"
            )

        # Suggested edit (editable textarea, always visible) + Copy button
        with ui.column().classes("w-full px-3 pt-1 pb-1 gap-1"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("SUGGESTED EDIT").classes(
                    "text-[9px] font-black tracking-[0.2em] uppercase opacity-50"
                )
                copy_btn = ui.button(
                    "Copy translation",
                    icon="content_copy",
                ).props("flat dense size=sm color=grey-6").classes("text-[10px]")

            suggestion_input = ui.textarea(
                value=reviewer_target,
                placeholder="Type your suggested translation here…",
            ).props("outlined dense autogrow").classes("w-full text-sm")

        # Comment (editable input, always visible)
        with ui.column().classes("w-full px-3 pt-1 pb-2 gap-1"):
            ui.label("COMMENT").classes(
                "text-[9px] font-black tracking-[0.2em] uppercase opacity-50"
            )
            comment_input = ui.input(
                value=reviewer_comment,
                placeholder="Leave a comment (optional)…",
            ).props("outlined dense").classes("w-full text-sm")

    # ------------------------------------------------------------------
    # Focus tracking — when the reviewer clicks into a suggestion
    # textarea, fire state.set_active(idx) so the intel panel
    # (Glossary + KG) populates for this segment. Also push a
    # prediction bundle so the ghost-text predictor tracks this
    # textarea (making click-to-insert from glossary/KG work).
    # This is the exact same pattern as segment_editor._on_active_change.
    # ------------------------------------------------------------------
    def _on_focus(_=None, i=idx, s=seg, si=suggestion_input):
        # CRITICAL: Do NOT set state.current["target"] to the suggestion
        # textarea value. WorkspaceState.set_active writes current["target"]
        # back to the old active segment's "target" field — if we set it
        # to the suggestion value (or empty), set_active would clobber
        # the old segment's original translation. Only call set_active
        # so the intel panel refreshes for this segment's source text.
        state.set_active(i)
        # Seed the predictor so insertAtCursor targets this textarea.
        src, tgt = parse_lang_pair(state.lang_pair)
        background_tasks.create(
            predictions.push_bundle(
                si.id, s.get("source", ""), src, tgt,
                tm, glossary, kg, client=page_client,
            ),
            name=f"review_push_bundle_{i}",
        )

    suggestion_input.on("focus", _on_focus)

    # Wire the Copy button — copies current translation into suggestion.
    def _do_copy(_=None, t=target, si=suggestion_input):
        si.set_value(t)
        seg["reviewer_target"] = t
        if t.strip():
            seg["reviewer_status"] = "suggested"
        rm.save_review(clone)

    copy_btn.on_click(_do_copy)

    # Autosave on edit
    def _on_suggest_change(e):
        seg["reviewer_target"] = e.value or ""
        if seg["reviewer_target"].strip():
            seg["reviewer_status"] = "suggested"
        elif seg["reviewer_comment"].strip():
            seg["reviewer_status"] = "commented"
        else:
            seg["reviewer_status"] = "pending"
        rm.save_review(clone)

    def _on_comment_change(e):
        seg["reviewer_comment"] = e.value or ""
        if seg["reviewer_target"].strip():
            seg["reviewer_status"] = "suggested"
        elif seg["reviewer_comment"].strip():
            seg["reviewer_status"] = "commented"
        else:
            seg["reviewer_status"] = "pending"
        rm.save_review(clone)

    suggestion_input.on_value_change(_on_suggest_change)
    comment_input.on_value_change(_on_comment_change)


# ======================================================================
# Page 3 — Merge view (side-by-side accept/reject)
# ======================================================================
@ui.page("/review/merge/{review_id}")
def page_review_merge(review_id: str):
    """Side-by-side merge view for the translator."""
    chrome = _install_chrome()
    if chrome is None:
        return
    dm, page_client, res = chrome

    clone = rm.load_review(review_id)
    if clone is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.label("Review not found.").classes("text-red-500 text-lg")
            ui.button("← Back", on_click=lambda: ui.navigate.to("/")).classes("mt-4")
        return

    original = res["load_project"](clone["original_project_id"])
    if original is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.label("Original project not found.").classes("text-red-500 text-lg")
            ui.button("← Back", on_click=lambda: ui.navigate.to("/")).classes("mt-4")
        return

    original["project_id"] = clone["original_project_id"]

    # ------------------------------------------------------------------
    # Top bar
    # ------------------------------------------------------------------
    with ui.card().classes(
        "w-full flex-row items-center justify-between rounded-none px-5 py-3 shrink-0"
    ).props("flat bordered"):
        with ui.row().classes("items-center gap-3"):
            ui.button(
                icon="arrow_back",
                on_click=lambda: ui.navigate.to(f"/review/{clone['original_project_id']}"),
            ).props("flat round dense").tooltip("Back to reviews")
            with ui.column().classes("gap-0"):
                ui.label(f"Merge: {review_id}").classes("text-sm font-bold leading-none")
                ui.label(clone.get("original_filename", "")).classes("text-[10px] opacity-60")
            from ui import settings as ui_settings
            ui_settings.dark_toggle_button(dm)

        with ui.row().classes("gap-2 items-center"):
            accepted_count = {"v": 0}

            def _get_accepted() -> set[int]:
                return {
                    seg.get("original_id")
                    for seg in clone["segments"]
                    if seg.get("_merge_accepted")
                }

            def _update_count():
                accepted_count["v"] = len(_get_accepted())
                count_label.set_text(
                    f"{accepted_count['v']} accepted · "
                    f"{len(clone['segments']) - accepted_count['v']} rejected"
                )

            count_label = ui.label("0 accepted · 0 rejected").classes(
                "text-xs font-bold opacity-70"
            )

            async def _merge_and_close():
                from ui.components import confirm_dialog

                accepted = _get_accepted()
                if not accepted:
                    ui.notify("No segments accepted to merge", type="warning")
                    return
                if not await confirm_dialog(
                    f"Merge {len(accepted)} accepted suggestions into the original project "
                    "and close this review? The funnel will be torn down.",
                    confirm_label="Merge & Close",
                ):
                    return
                async with busy_overlay("Merging…"):
                    rm.merge_review_into_original(original, clone, accepted)
                    res["save_project"](original)
                    rm.stop_funnel()
                    clone["status"] = rm.STATUS_MERGED
                    clone["funnel_active"] = False
                    rm.save_review(clone)
                ui.notify("Merge complete. Review closed.", type="positive")
                ui.navigate.to(f"/review/{clone['original_project_id']}")

            async def _merge_and_reopen():
                from ui.components import confirm_dialog

                accepted = _get_accepted()
                if not await confirm_dialog(
                    f"Merge {len(accepted)} accepted suggestions, then reopen the review "
                    "for the reviewer with a fresh funnel?",
                    confirm_label="Merge & Reopen",
                    confirm_color="primary",
                ):
                    return
                async with busy_overlay("Merging & reopening…"):
                    rm.merge_review_into_original(original, clone, accepted)
                    res["save_project"](original)
                    validity = clone.get("funnel_validity_days", rm.DEFAULT_VALIDITY_DAYS)
                    url = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: rm.reopen_review(clone, validity)
                    )
                ui.notify(f"Reopened. Funnel: {url}", type="positive", timeout=5000)
                ui.navigate.to(f"/review/{clone['original_project_id']}")

            ui.button("Merge & Reopen", icon="replay", on_click=_merge_and_reopen).props(
                "outline color=primary"
            ).classes("text-[11px]")
            ui.button("Merge & Close", icon="check_circle", on_click=_merge_and_close).props(
                "unelevated color=positive"
            ).classes("text-[11px]")

    # ------------------------------------------------------------------
    # Segment comparison list
    # ------------------------------------------------------------------
    with ui.column().classes("w-full max-w-4xl mx-auto px-4 py-2 gap-2"):
        ui.label(
            "Review each suggestion. Click Accept or Reject for each segment, "
            "then merge at the top."
        ).classes("text-[10px] opacity-50 pb-2")

        scroll = ui.scroll_area().classes("w-full h-[78vh]")
        with scroll:
            with ui.column().classes("w-full gap-2"):
                for seg in clone["segments"]:
                    _build_merge_row(seg, clone, _update_count)


def _build_merge_row(seg: dict, clone: dict, update_count) -> None:
    """Build one comparison row in the merge view."""
    _oid = seg.get("original_id")
    orig_id: int = _oid if _oid is not None else seg.get("id", 0)
    target = seg.get("target", "")
    reviewer_target = seg.get("reviewer_target", "")
    reviewer_comment = seg.get("reviewer_comment", "")

    # Skip segments with no suggestion and no comment
    has_suggestion = bool(reviewer_target.strip())
    has_comment = bool(reviewer_comment.strip())

    if not has_suggestion and not has_comment:
        # Still show it, but greyed out — no changes suggested.
        with ui.card().props("flat bordered").classes(
            "w-full p-3 rounded-xl opacity-40"
        ):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label(f"#{orig_id + 1}").classes(
                    "text-[10px] font-black opacity-50 w-10 shrink-0"
                )
                ui.label("No changes suggested").classes("text-xs italic opacity-50 flex-1")
        return

    # Init the per-segment accepted flag
    seg["_merge_accepted"] = False

    with ui.card().props("flat bordered").classes("w-full p-3 rounded-xl"):
        with ui.row().classes("w-full items-center gap-3 mb-2"):
            ui.label(f"#{orig_id + 1}").classes(
                "text-[10px] font-black opacity-50 w-10 shrink-0"
            )
            if has_suggestion:
                ui.badge("SUGGESTED", color="warning").classes("text-[9px]")
            if has_comment:
                ui.badge("COMMENT", color="info").classes("text-[9px]")

            # Accept/Reject toggle buttons
            def _accept(_=None, s=seg, u=update_count):
                s["_merge_accepted"] = True
                accept_btn.props("unelevated color=positive")
                reject_btn.props("flat color=grey-6")
                u()

            def _reject(_=None, s=seg, u=update_count):
                s["_merge_accepted"] = False
                accept_btn.props("flat color=grey-6")
                reject_btn.props("unelevated color=negative")
                u()

            accept_btn = ui.button("Accept", icon="check", on_click=_accept).props(
                "flat color=grey-6"
            ).classes("text-[10px] ml-auto")
            reject_btn = ui.button("Reject", icon="close", on_click=_reject).props(
                "flat color=grey-6"
            ).classes("text-[10px]")

        # Original target
        ui.label("ORIGINAL").classes("text-[9px] font-black opacity-50")
        ui.label(target).classes("text-sm leading-relaxed w-full mb-2").style(
            'font-family: "Inter", sans-serif; line-height: 1.625;'
        )

        # Suggested (with diff if available)
        if has_suggestion:
            ui.label("SUGGESTED").classes("text-[9px] font-black opacity-50")
            diff_html = _render_diff(target, reviewer_target)
            ui.html(diff_html).classes("text-sm leading-relaxed w-full mb-2").style(
                'font-family: "Inter", sans-serif; line-height: 1.625;'
            )

        # Comment
        if has_comment:
            with ui.row().classes("w-full items-start gap-2 mt-1"):
                ui.icon("comment", size="14px").props("color=info").classes("shrink-0 mt-1")
                ui.label(reviewer_comment).classes(
                    "text-xs italic bg-blue-500/10 px-3 py-2 rounded-lg flex-1"
                )


def _render_diff(original: str, suggested: str) -> str:
    """Render a word-level diff as styled HTML.

    Uses ``difflib.ndiff`` and wraps removed/added spans with color classes.
    This follows the same ``ui.html`` pattern as the ghost-text overlay —
    a single styled span, not a full template.
    """
    if not original or not suggested:
        return html_lib.escape(suggested)

    orig_words = original.split()
    sugg_words = suggested.split()
    matcher = difflib.SequenceMatcher(None, orig_words, sugg_words)

    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(html_lib.escape(" ".join(orig_words[i1:i2])))
        elif tag == "delete":
            text = html_lib.escape(" ".join(orig_words[i1:i2]))
            parts.append(
                f'<span style="color: #ef4444; text-decoration: line-through;">{text}</span>'
            )
        elif tag == "insert":
            text = html_lib.escape(" ".join(sugg_words[j1:j2]))
            parts.append(
                f'<span style="color: #22c55e; font-weight: 600;">{text}</span>'
            )
        elif tag == "replace":
            del_text = html_lib.escape(" ".join(orig_words[i1:i2]))
            ins_text = html_lib.escape(" ".join(sugg_words[j1:j2]))
            parts.append(
                f'<span style="color: #ef4444; text-decoration: line-through;">{del_text}</span>'
                f' <span style="color: #22c55e; font-weight: 600;">{ins_text}</span>'
            )
    return " ".join(parts)


# ======================================================================
# Page registration helper
# ======================================================================
__all__ = ["page_review", "page_review_ext", "page_review_merge"]