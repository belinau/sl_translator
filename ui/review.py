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
from translate_core import comments as cm
from ui.comments_panel import build as build_comments_panel
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
    save_pair_to_tm = app_state.save_pair_to_tm

    needed = (tm, glossary, kg, qa_engine, parse_lang_pair, config, load_project, save_project, save_pair_to_tm)
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
        "save_pair_to_tm": save_pair_to_tm,
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
                def _do_copy(_, t=url_text):
                    ui.run_javascript(
                        f"""(function() {{
                            var text = {json.dumps(t)};
                            if (navigator.clipboard && navigator.clipboard.writeText) {{
                                navigator.clipboard.writeText(text).catch(function() {{
                                    _fallbackCopy(text);
                                }});
                            }} else {{
                                _fallbackCopy(text);
                            }}
                            function _fallbackCopy(text) {{
                                var ta = document.createElement('textarea');
                                ta.value = text;
                                ta.style.position = 'fixed';
                                ta.style.opacity = '0';
                                document.body.appendChild(ta);
                                ta.select();
                                try {{ document.execCommand('copy'); }} catch(e) {{}}
                                document.body.removeChild(ta);
                            }}
                        }})()"""
                    )
                    ui.notify("Link copied to clipboard", type="positive", timeout=2000)

                ui.button(
                    icon="content_copy",
                    on_click=_do_copy,
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

            ui.button(
                "Export table",
                icon="grid_on",
                on_click=lambda _, r=r: _export_review_table(r),
            ).props("flat dense color=teal").classes("text-[11px]").tooltip(
                "Download bilingual review table (DOCX)"
            )

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
                "commented": "Only with comments",
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

        # Commented-only info
        commented_segs = [s for s in segs if s.get("comments")]
        commented_info = ui.label(
            f"{len(commented_segs)} segment{'s' if len(commented_segs) != 1 else ''} with comments"
        ).classes("text-xs opacity-70")
        commented_info.set_visibility(False)

        selected_count_label = ui.label(f"{len(done_segs)} segments selected").classes(
            "text-xs font-medium opacity-70"
        )

        def _on_mode_change(e):
            range_row.set_visibility(e.value == "range")
            commented_info.set_visibility(e.value == "commented")
            if e.value == "all_done":
                selected_count_label.set_text(f"{len(done_segs)} segments selected")
            elif e.value == "range":
                _update_range_count()
            elif e.value == "commented":
                selected_count_label.set_text(
                    f"{len(commented_segs)} segments selected"
                )

        def _update_range_count():
            f = int(range_from.value or 0)
            t = int(range_to.value or 0)
            count = max(0, t - f + 1)
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
                elif mode.value == "commented":
                    selected_ids = [s["id"] for s in segs if s.get("comments")]
                else:
                    selected_ids = [s["id"] for s in done_segs]

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
    r["funnel_active"] = False
    rm.save_review(r)
    rm.stop_funnel()
    ui.notify("Funnel stopped", type="warning")
    _render_reviews(container, project_id, client)


async def _restart_funnel(r: dict, container, project_id, client) -> None:
    validity = r.get("funnel_validity_days", rm.DEFAULT_VALIDITY_DAYS)
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


async def _reopen_review(r: dict, container, project_id, client) -> None:
    validity = r.get("funnel_validity_days", rm.DEFAULT_VALIDITY_DAYS)
    try:
        async with busy_overlay("Reopening review…"):
            url = await asyncio.get_running_loop().run_in_executor(
                None, lambda: rm.reopen_review(r, validity)
            )
        ui.notify(f"Review reopened. Funnel: {url}", type="positive", timeout=5000)
    except Exception as e:
        ui.notify(f"Failed: {e}", type="negative")
    _render_reviews(container, project_id, client)


def _export_review_table(r: dict) -> None:
    """Export a review clone as a bilingual table DOCX and trigger download."""
    from translate_core.doc_parser import DocumentParser

    clone = rm.load_review(r["review_id"])
    if clone is None:
        ui.notify("Review data not found", type="negative")
        return
    out = rm.REVIEWS_DIR / f"review_table_{clone['review_id']}.docx"
    try:
        DocumentParser().compile_review_table_docx(clone, out)
        if out.exists():
            filename = f"review_{clone.get('original_filename', 'table')}.docx"
            ui.download(out.read_bytes(), filename)
            out.unlink(missing_ok=True)
            return
    except Exception as e:
        log.error("review table export: %s", e)
    ui.notify("Export failed", type="negative")


async def _delete_review(review_id: str, container, project_id, client) -> None:
    from ui.components import confirm_dialog
    if await confirm_dialog(
        f"Delete review {review_id}? This cannot be undone.",
        confirm_label="Delete",
    ):
        rm.delete_review(review_id)
        ui.notify("Review deleted", type="warning")
        _render_reviews(container, project_id, client)


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
        "w-full flex-row items-center justify-between rounded-none px-5 py-2 shrink-0 sticky top-0 z-50 bg-white/95 dark:bg-slate-900/95 backdrop-blur-sm"
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
            # Save indicator — same pattern as workspace.py:174-194
            # but driven by the reviewer's own save calls (rm.save_review)
            # rather than WorkspaceState's autosave, since the reviewer
            # page doesn't use WorkspaceState for segment persistence.
            save_label = ui.label("✓ Saved").classes(
                "text-[9px] font-bold uppercase tracking-wider text-positive"
            )

            def _set_save_status(status: str):
                try:
                    with page_client:
                        if status == "saved":
                            text, color = "✓ Saved", "text-positive"
                        elif status == "saving":
                            text, color = "Saving…", "text-amber-500"
                        else:
                            text, color = "● Unsaved", "text-grey-500"
                        save_label.set_text(text)
                        save_label.classes(
                            remove="text-positive text-amber-500 text-grey-500",
                            add=color,
                        )
                except Exception:
                    pass

            # Reviewer name input
            reviewer_input = ui.input(
                placeholder="Your name",
                value=clone.get("reviewer_name", ""),
            ).props("outlined dense").classes("w-40 text-xs")
            ui.button(icon="save", on_click=lambda: _save_reviewer_name(clone, reviewer_input, _set_save_status)).props(
                "flat round dense color=positive"
            ).tooltip("Save your name")
            from ui import settings as ui_settings
            ui_settings.dark_toggle_button(dm)

    # ------------------------------------------------------------------
    # Main content — Glossary + Find & Replace, then segment list.
    # KG is intentionally excluded: it confuses reviewers who only need
    # the glossary for terminology and a search/replace tool for bulk
    # edits across their suggested translations.
    # ------------------------------------------------------------------
    from ui.intel_panel import _truncate
    from ui.kg_search import highlight_query
    from nicegui import run
    import re

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

            # ---------------------------------------- Find & Replace in targets
            with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.icon("find_replace", size="16px").props("color=primary")
                    ui.label("FIND & REPLACE IN REVIEW EDITS").classes(
                        "text-[10px] font-black tracking-[.3em] opacity-70"
                    )
                fr_find_input = (
                    ui.input(placeholder="Find in your suggested edits")
                    .props("dense outlined clearable")
                    .classes("w-full")
                )
                fr_replace_input = (
                    ui.input(placeholder="Replace with")
                    .props("dense outlined clearable")
                    .classes("w-full")
                )
                with ui.row().classes("w-full items-center gap-2 mt-1"):
                    fr_match_case = ui.checkbox("Match case", value=False).classes("text-xs")
                    fr_search_btn = ui.button("Search", icon="search").props(
                        "flat dense color=primary"
                    ).classes("text-xs")
                    fr_replace_btn = ui.button(
                        "Replace All", icon="find_replace"
                    ).props("flat dense color=warning").classes("text-xs")
                fr_results = ui.column().classes("w-full gap-1 mt-2")

            # ----------------------------------------------------------------
            # Insert helper — routes through the predictor's JS runtime
            # so click-to-insert targets the focused suggestion textarea.
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
            # Find & Replace — searches reviewer_target across all segments
            # in this review. Replace All mutates the clone and saves.
            # ----------------------------------------------------------------
            def _fr_matches(text: str, q: str) -> bool:
                if not q:
                    return False
                if fr_match_case.value:
                    return q in (text or "")
                return q.lower() in (text or "").lower()

            def _fr_count(text: str, q: str) -> int:
                if not q:
                    return 0
                flags = 0 if fr_match_case.value else re.IGNORECASE
                return len(re.findall(re.escape(q), text or "", flags))

            def _fr_replace(text: str, q: str, repl: str) -> str:
                if not q:
                    return text
                flags = 0 if fr_match_case.value else re.IGNORECASE
                return re.sub(re.escape(q), lambda m: repl, text or "", flags=flags)

            def _fr_run_search() -> None:
                q = (fr_find_input.value or "").strip()
                fr_results.clear()
                if not q:
                    return
                hits: list[tuple[int, dict, int]] = []
                for i, s in enumerate(clone["segments"]):
                    t = s.get("reviewer_target", "")
                    if _fr_matches(t, q):
                        hits.append((i, s, _fr_count(t, q)))
                with fr_results:
                    if not hits:
                        ui.label("No suggested edits match.").classes(
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
                                _oid = s.get("original_id")
                                orig_id: int = _oid if _oid is not None else s.get("id", idx)
                                ui.label(f"#{orig_id + 1}").classes(
                                    "text-[10px] font-black tabular-nums opacity-50"
                                )
                                ui.label(f"{n_occ}×").classes(
                                    "text-[9px] opacity-50"
                                )
                            ui.html(
                                highlight_query(s.get("reviewer_target", ""), q)
                            ).classes("text-xs leading-snug").style(
                                "white-space:normal;word-break:break-word"
                            )

            def _fr_run_replace() -> None:
                q = (fr_find_input.value or "").strip()
                repl = fr_replace_input.value or ""
                if not q:
                    ui.notify("Enter a search term first.", type="warning")
                    return
                matched: list[tuple[int, dict, int]] = []
                for i, s in enumerate(clone["segments"]):
                    t = s.get("reviewer_target", "")
                    if _fr_matches(t, q):
                        matched.append((i, s, _fr_count(t, q)))
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
                        f"in {len(matched)} suggested edit"
                        f"{'s' if len(matched) != 1 else ''}?"
                    ).classes("text-sm opacity-80").style(
                        "white-space:normal;word-break:break-word"
                    )
                    ui.label("Preview:").classes("text-[10px] font-bold opacity-60 mt-1")
                    with ui.column().classes("w-full gap-1"):
                        for idx, s, _n in matched[:3]:
                            old_t = s.get("reviewer_target", "")
                            new_t = _fr_replace(old_t, q, repl)
                            with ui.row().classes("w-full gap-1 items-start"):
                                _oid = s.get("original_id")
                                orig_id: int = _oid if _oid is not None else s.get("id", idx)
                                ui.label(f"#{orig_id + 1}").classes(
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
                                f"… and {len(matched) - 3} more"
                            ).classes("text-[9px] italic opacity-50")

                    with ui.row().classes("w-full justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dialog.close).props(
                            "flat dense color=grey-6"
                        )
                        def _confirm() -> None:
                            dialog.close()
                            for i, s, _n in matched:
                                old_t = s.get("reviewer_target", "")
                                s["reviewer_target"] = _fr_replace(old_t, q, repl)
                                if s["reviewer_target"].strip():
                                    s["reviewer_status"] = "suggested"
                            rm.save_review(clone)
                            ui.notify(
                                f"Replaced {total_occ} occurrence"
                                f"{'s' if total_occ != 1 else ''} "
                                f"in {len(matched)} edit"
                                f"{'s' if len(matched) != 1 else ''}.",
                                type="positive",
                            )
                            if _set_save_status:
                                _set_save_status("saved")
                            _fr_run_search()
                        ui.button("Replace", on_click=_confirm).props(
                            "unelevated dense color=warning"
                        )
                    dialog.open()

            fr_search_btn.on("click", lambda _e: _fr_run_search())
            fr_find_input.on("keydown.enter", lambda _e: _fr_run_search())
            fr_replace_btn.on("click", lambda _e: _fr_run_replace())

            # ----------------------------------------------------------------
            # Refresh Glossary on segment change.
            # ----------------------------------------------------------------
            def _refresh_all():
                sid = id(state)
                background_tasks.create_lazy(_refresh_gl(), name=f"review_gl_refresh_{sid}")

            state.subscribe("active_index", _refresh_all)
            _refresh_all()

        # Segment list below the intel panel
        with ui.column().classes("w-full max-w-4xl px-4 py-2 gap-0"):
            ui.label("Review segments — suggest changes in the edit field, leave comments if needed. Use 'Copy' to start from the current translation.").classes(
                "text-[10px] opacity-50 px-2 pb-2"
            )
            _build_review_segment_list(clone, state, deps, page_client, _set_save_status)

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


def _save_reviewer_name(clone: dict, input_el, set_save_status=None) -> None:
    clone["reviewer_name"] = input_el.value or ""
    if set_save_status:
        set_save_status("saving")
    rm.save_review(clone)
    if set_save_status:
        set_save_status("saved")
    ui.notify("Name saved", type="positive", timeout=1000)


def _on_comments_change(seg, clone, set_save_status):
    """Persist the review after the comments panel mutates a segment.

    The shared panel re-renders itself on add/delete; this callback
    re-derives reviewer_status (a comment-only change can flip the
    status between 'commented' and 'pending') and saves the clone.
    """
    has_target = bool(seg.get("reviewer_target", "").strip())
    has_comment = any(
        c.get("author") == cm.AUTHOR_REVIEWER
        and c.get("round") == seg.get("_clone_round", 1)
        for c in cm.ensure_comments(seg)
    )
    if has_target:
        seg["reviewer_status"] = "suggested"
    elif has_comment:
        seg["reviewer_status"] = "commented"
    else:
        seg["reviewer_status"] = "pending"
    if set_save_status:
        set_save_status("saving")
    rm.save_review(clone)
    if set_save_status:
        set_save_status("saved")


def _build_review_segment_list(
    clone: dict, state, deps: dict, page_client, set_save_status=None,
) -> None:
    """Build the scrollable list of fully-expanded review segment cards.

    Every segment is shown in full — no collapse/expand. When the
    reviewer focuses a suggestion textarea, state.set_active(idx) fires
    so the glossary panel populates for that segment, and
    predictions.push_bundle seeds the ghost-text predictor for that
    textarea.
    """
    scroll = ui.scroll_area().classes("w-full h-[80vh]")
    with scroll:
        with ui.column().classes("w-full gap-1"):
            for i, seg in enumerate(clone["segments"]):
                _build_segment_card(seg, i, clone, state, deps, page_client, set_save_status)


def _build_segment_card(
    seg: dict, idx: int, clone: dict, state, deps: dict, page_client,
    set_save_status=None,
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
        # Comments — history (read-only) + reviewer's current-round input
        with ui.column().classes("w-full px-3 pt-1 pb-2 gap-1"):
            ui.label("COMMENTS").classes(
                "text-[9px] font-black tracking-[0.2em] uppercase opacity-50"
            )
            comments_handle = build_comments_panel(
                on_change=lambda: _on_comments_change(seg, clone, set_save_status)
            )
            comments_handle["rebuild"](
                seg, cm.AUTHOR_REVIEWER,
                round=seg.get("_clone_round", 1),
                review_id=clone.get("review_id", ""),
            )

    # ------------------------------------------------------------------
    # Focus tracking — when the reviewer clicks into a suggestion
    # textarea, fire state.set_active(idx) so the glossary panel
    # populates for this segment. Also push a prediction bundle so the
    # ghost-text predictor tracks this textarea (making click-to-insert
    # from glossary work).
    # ------------------------------------------------------------------
    def _on_focus(_=None, i=idx, s=seg, si=suggestion_input):
        # CRITICAL: Do NOT set state.current["target"] to the suggestion
        # textarea value. WorkspaceState.set_active writes current["target"]
        # back to the old active segment's "target" field — if we set it
        # to the suggestion value (or empty), set_active would clobber
        # the old segment's original translation. Only call set_active
        # so the glossary refreshes for this segment's source text.
        state.set_active(i)
        # Seed the predictor so insertAtCursor targets this textarea.
        src, tgt = parse_lang_pair(state.lang_pair)
        background_tasks.create_lazy(
            predictions.push_bundle(
                si.id, s.get("source", ""), src, tgt,
                tm, glossary, kg, client=page_client,
            ),
            name=f"review_push_bundle_{id(state)}",
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

    # Autosave on edit — updates the save indicator (same pattern as
    # workspace.py's WorkspaceState save_status subscriber).
    def _on_suggest_change(e):
        seg["reviewer_target"] = e.value or ""
        has_target = bool(seg["reviewer_target"].strip())
        has_comment = any(
            c.get("author") == cm.AUTHOR_REVIEWER
            and c.get("round") == seg.get("_clone_round", 1)
            for c in cm.ensure_comments(seg)
        )
        if has_target:
            seg["reviewer_status"] = "suggested"
        elif has_comment:
            seg["reviewer_status"] = "commented"
        else:
            seg["reviewer_status"] = "pending"
        if set_save_status:
            set_save_status("saving")
        rm.save_review(clone)
        if set_save_status:
            set_save_status("saved")

    suggestion_input.on_value_change(_on_suggest_change)


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
                    # Upsert each accepted segment to TM — same as the
                    # editor's confirm button does via save_pair_to_tm.
                    lang_pair = original.get("lang_pair", "en->sl")
                    _save_pairs_to_tm = res["save_pair_to_tm"]
                    orig_map = {s["id"]: s for s in original["segments"]}
                    for cseg in clone["segments"]:
                        oid = cseg.get("original_id")
                        if oid not in accepted:
                            continue
                        orig_seg = orig_map.get(oid)
                        if orig_seg and orig_seg.get("target", "").strip() and orig_seg.get("source", "").strip():
                            _save_pairs_to_tm(orig_seg["source"], orig_seg["target"], lang_pair)
                    clone["status"] = rm.STATUS_MERGED
                    clone["funnel_active"] = False
                    rm.save_review(clone)
                    rm.stop_funnel()
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
                    # Upsert each accepted segment to TM — same as the
                    # editor's confirm button does via save_pair_to_tm.
                    lang_pair = original.get("lang_pair", "en->sl")
                    _save_pairs_to_tm = res["save_pair_to_tm"]
                    orig_map = {s["id"]: s for s in original["segments"]}
                    for cseg in clone["segments"]:
                        oid = cseg.get("original_id")
                        if oid not in accepted:
                            continue
                        orig_seg = orig_map.get(oid)
                        if orig_seg and orig_seg.get("target", "").strip() and orig_seg.get("source", "").strip():
                            _save_pairs_to_tm(orig_seg["source"], orig_seg["target"], lang_pair)
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

    # Skip segments with no suggestion and no comment
    has_suggestion = bool(reviewer_target.strip())
    has_comment = bool(cm.ensure_comments(seg))

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

        # Comments — full history, read-only
        if has_comment:
            with ui.column().classes("w-full mt-1 gap-1"):
                ui.label("COMMENTS").classes(
                    "text-[9px] font-black tracking-[0.2em] uppercase opacity-50"
                )
                panel = build_comments_panel(editable=False)
                panel["rebuild"](
                    seg, cm.AUTHOR_REVIEWER,
                    round=seg.get("_clone_round", 1),
                    review_id=clone.get("review_id", ""),
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