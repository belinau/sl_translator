"""KG editor — KG Review page. NiceGUI port of kg_editor_ui.py page_kg_review (lines 1086-1228).

Nodes already in the graph that look wrong. Unlike Extraction Review
(candidates to add), these are fixed in place: edit, reclassify, delete,
or keep. Run a scan to (re)build the list.
"""
from __future__ import annotations

import json
from collections import Counter

from nicegui import run, ui

from translate_core.kg_review_ops import (
    RECLASS_AGENT_ROLES,
    RECLASS_PROJECT_TYPES,
    RECLASS_TARGETS,
    drop_kg_review,
    load_kg_review,
    reclassify_live_node,
)

PAGE_SIZE = 15


@ui.page("/kg/kg-review")
def page_kg_review():
    from ui.kg_editor.common import build_reclass_inputs, kg_frame

    kg, _glossary = kg_frame("/kg/kg-review")
    if kg is None:
        return

    # ── State closures ────────────────────────────────────────────────────
    reason_ref: dict = {"value": "all"}
    page_ref: dict = {"value": 1}
    _reason_counts: dict = {}  # updated each render for format_func

    results = ui.column().classes("w-full")

    def render():
        items = load_kg_review()

        results.clear()
        with results:
            ui.label("KG Review — dubious live entries").classes("text-lg font-bold")
            ui.label(
                "Nodes already in the graph that look wrong. Unlike Extraction Review "
                "(candidates to add), these are fixed in place: edit, reclassify, delete, "
                "or keep. Run a scan to (re)build the list."
            ).classes("text-caption q-mb-sm")

            # ── Scan button ───────────────────────────────────────────────
            with ui.row().classes("items-center q-gutter-sm q-mb-md"):
                async def _on_scan():
                    try:
                        from scripts.flag_kg_review import flag_dubious
                    except ImportError:
                        ui.notify(
                            "Scan unavailable: scripts/flag_kg_review.py not found",
                            type="warning",
                        )
                        return
                    from translate_core.kg_review_ops import (
                        KG_DISMISSED_PATH,
                        KG_REVIEW_PATH,
                    )

                    data_nodes = [d for _, d in kg.G.nodes(data=True)]
                    data_edges = [
                        {"source": u, "target": v, **d}
                        for u, v, d in kg.G.edges(data=True)
                    ]
                    try:
                        dismissed = (
                            set(
                                json.loads(
                                    KG_DISMISSED_PATH.read_text(encoding="utf-8")
                                )
                            )
                            if KG_DISMISSED_PATH.exists()
                            else set()
                        )
                    except Exception:
                        dismissed = set()
                    flagged = flag_dubious(data_nodes, data_edges, dismissed)
                    KG_REVIEW_PATH.write_text(
                        json.dumps(flagged, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    ui.notify(f"Flagged {len(flagged)} dubious nodes.", type="positive")
                    render()

                ui.button("Scan / rescan KG", icon="search", on_click=_on_scan).props(
                    "flat dense no-caps color=primary"
                )

                ui.label(
                    "No review list yet (or empty). Press Scan / rescan KG."
                ).classes("text-grey-6 q-pa-md italic")
                return

            # ── Reason filter ──────────────────────────────────────────────
            reasons = Counter(it.get("reason") for it in items)
            reason_opts = ["all"] + sorted(reasons)

            def _fmt_reason(r):
                if r == "all":
                    return f"all ({len(items)})"
                return f"{r} ({reasons.get(r, 0)})"

            _reason_counts.clear()
            _reason_counts["all"] = len(items)
            _reason_counts.update(reasons)
            reason_select.set_options(reason_opts)

            shown = [
                it
                for it in items
                if reason_ref["value"] == "all" or it.get("reason") == reason_ref["value"]
            ]

            # ── Pagination ─────────────────────────────────────────────────
            total_pages = max(1, (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page = min(page_ref["value"], total_pages)
            page_ref["value"] = current_page
            page_start = (current_page - 1) * PAGE_SIZE

            if total_pages > 1:
                pag = ui.pagination(
                    min=1,
                    max=total_pages,
                    direction_links=True,
                    value=current_page,
                    on_change=lambda e: _update_page(e.value),
                ).classes("q-mb-md")

            # ── edit_ptypes (mirrors Streamlit lines 1127-1129) ─────────────
            edit_ptypes = list(
                dict.fromkeys(
                    [
                        "book_translation",
                        "article_translation",
                        "festival_programme",
                        "exhibition_catalogue",
                        "cited_container",
                    ]
                    + RECLASS_PROJECT_TYPES
                )
            )

            # ── Per-item list ──────────────────────────────────────────────
            for it in shown[page_start : page_start + PAGE_SIZE]:
                _render_item(it, items, kg, edit_ptypes, render)

    # ── Filter/pagination helpers ─────────────────────────────────────────
    def _update_reason(e, sel):
        reason_ref["value"] = sel.value or "all"
        page_ref["value"] = 1
        render()

    def _update_page(page_num):
        page_ref["value"] = page_num or 1
        render()

    # ── Per-item expansion ─────────────────────────────────────────────────
    def _render_item(it, items, kg, edit_ptypes, render_fn):
        nid = it["id"]
        ntype = it.get("type")

        # Skip nodes that have been removed elsewhere
        if not kg.G.has_node(nid):
            return

        label = (
            it.get("name")
            or it.get("title_orig") or it.get("title")
            or it.get("title_translation")
            or nid
        )
        with ui.expansion(f"{ntype}: {label[:70]}", caption=it.get("reason")).classes(
            "w-full"
        ):
            ui.label(f"id: {nid}").classes("text-[11px] opacity-45")
            # ── Type-specific summary ─────────────────────────────────────
            if ntype == "source_text":
                ui.markdown(
                    f"**title:** `{it.get('title')}`  ·  **orig:** `{it.get('title_orig')}`  ·  "
                    f"**translation:** `{it.get('title_translation')}`  ·  **year:** `{it.get('year')}`  ·  "
                    f"**type:** `{it.get('project_type')}`"
                ).classes("text-sm")
            elif ntype == "agent":
                ui.markdown(
                    f"**name:** `{it.get('name')}`  ·  **role:** `{it.get('role')}`"
                ).classes("text-sm")

            # ── Action select ──────────────────────────────────────────────
            action_opts = ["Edit fields", "Reclassify type", "Delete node", "Keep (dismiss)"]
            action_select = ui.select(
                action_opts, value="Edit fields", label="Action:"
            ).classes("w-full q-mb-sm")

            # Action container — swapped on select change
            action_area = ui.column().classes("w-full")

            def _on_action_change():
                action_area.clear()
                with action_area:
                    _render_action(
                        action_select.value, it, items, kg, edit_ptypes, render_fn
                    )

            action_select.on("update:model-value", _on_action_change)

            # Initial render
            with action_area:
                _render_action("Edit fields", it, items, kg, edit_ptypes, render_fn)

    # ── Action rendering ──────────────────────────────────────────────────
    def _render_action(action, it, items, kg, edit_ptypes, render_fn):
        nid = it["id"]
        ntype = it.get("type")

        if action == "Edit fields":
            _render_edit_fields(it, items, kg, edit_ptypes, render_fn)

        elif action == "Reclassify type":
            _render_reclassify(it, items, kg, render_fn)

        elif action == "Delete node":
            _render_delete(it, items, kg, render_fn)

        elif action == "Keep (dismiss)":
            _render_keep(it, items, render_fn)

    # ── Edit fields ───────────────────────────────────────────────────────
    def _render_edit_fields(it, items, kg, edit_ptypes, render_fn):
        nid = it["id"]
        ntype = it.get("type")

        with ui.card().classes("w-full p-3 gap-2").props("flat bordered"):
            if ntype == "source_text":
                e_torig = ui.input("Title (orig):", value=it.get("title_orig") or it.get("title_en") or "").classes(
                    "w-full"
                )
                e_ttrans = ui.input("Title (translation):", value=it.get("title_translation") or it.get("title_sl") or "").classes(
                    "w-full"
                )
                e_title = ui.input(
                    "Display title:", value=it.get("title") or ""
                ).classes("w-full")
                e_yr = ui.input("Year:", value=str(it.get("year") or "")).classes(
                    "w-full"
                )
                cur_pt = it.get("project_type") or "cited_work"
                pt_opts = list(dict.fromkeys([cur_pt] + edit_ptypes))
                e_pt = ui.select(pt_opts, value=cur_pt, label="Project type:").classes(
                    "w-full"
                )
                async def _on_save_source():
                    yr_str = e_yr.value.strip()
                    try:
                        yr = int(yr_str) if yr_str else None
                    except ValueError:
                        yr = None
                    await run.io_bound(
                        kg.update_source_text_node,
                        nid,
                        title=e_title.value or None,
                        year=yr,
                        project_type=e_pt.value,
                        title_orig=e_torig.value.strip() or None,
                        title_translation=e_ttrans.value.strip() or None,
                    )
                    await run.io_bound(kg.save)
                    drop_kg_review(items, it)
                    ui.notify("Saved.", type="positive")
                    render_fn()

                ui.button("Save", icon="save", on_click=_on_save_source).props(
                    "flat dense no-caps color=primary"
                )

            elif ntype == "agent":
                e_name = ui.input("Name:", value=it.get("name") or "").classes("w-full")
                cur_role = it.get("role") or "author"
                role_opts = list(dict.fromkeys([cur_role] + RECLASS_AGENT_ROLES))
                e_role = ui.select(role_opts, value=cur_role, label="Role:").classes(
                    "w-full"
                )

                async def _on_save_agent():
                    await run.io_bound(
                        kg.update_agent_node,
                        nid,
                        name=e_name.value or None,
                        role=e_role.value,
                    )
                    await run.io_bound(kg.save)
                    drop_kg_review(items, it)
                    ui.notify("Saved.", type="positive")
                    render_fn()

                ui.button("Save", icon="save", on_click=_on_save_agent).props(
                    "flat dense no-caps color=primary"
                )

            else:
                ui.label(
                    "No inline editor for this type — use Reclassify or Delete."
                ).classes("text-caption italic")

    # ── Reclassify type ───────────────────────────────────────────────────
    def _render_reclassify(it, items, kg, render_fn):
        nid = it["id"]
        label_to_target = {v: k for k, v in RECLASS_TARGETS.items()}

        choice_select = ui.select(
            list(RECLASS_TARGETS.values()),
            value=list(RECLASS_TARGETS.values())[0],
            label="New type:",
        ).classes("w-full q-mb-sm")

        reclass_area = ui.column().classes("w-full")

        def _build_reclass_form():
            """(Re)build the reclassify form inside reclass_area."""
            target = label_to_target.get(choice_select.value, "agent")
            cands = {
                "primary": (
                    it.get("name")
                    or it.get("title_orig") or it.get("title")
                    or it.get("title_translation")
                    or nid
                ),
                "name": it.get("name")
                or it.get("title_orig") or it.get("title")
                or it.get("title_translation")
                or nid,
                "title_orig": it.get("title_orig") or it.get("title_en") or "",
                "title_translation": it.get("title_translation") or it.get("title_sl") or "",
                "author": "",
                "year": it.get("year"),
                "city": "",
            }
            ui.label(
                f"Reclassify → {choice_select.value} "
                "(old node's edges are migrated, then it is removed)."
            ).classes("text-caption q-mb-sm")
            f = build_reclass_inputs(target, cands)

            async def _on_reclassify():
                fields = {k: v.value for k, v in f.items()}
                err = reclassify_live_node(kg, nid, target, fields)
                if err:
                    ui.notify(err, type="negative")
                    return
                await run.io_bound(kg.save)
                drop_kg_review(items, it)
                ui.notify(f"Reclassified as {choice_select.value}.", type="positive")
                render_fn()

            ui.button(
                f"Reclassify as {choice_select.value}",
                icon="swap_horiz",
                on_click=_on_reclassify,
            ).props("flat dense no-caps color=primary")

        def _on_choice_change():
            reclass_area.clear()
            with reclass_area:
                _build_reclass_form()

        choice_select.on("update:model-value", _on_choice_change)

        # Initial render
        with reclass_area:
            _build_reclass_form()

    # ── Delete node ────────────────────────────────────────────────────────
    def _render_delete(it, items, kg, render_fn):
        nid = it["id"]

        with ui.card().classes("w-full p-3 gap-2").props("flat bordered"):
            ui.label("Removes the node and its edges from the KG.").classes(
                "text-caption text-negative"
            )

            async def _on_delete():
                await run.io_bound(kg.remove_node, nid)
                await run.io_bound(kg.save)
                drop_kg_review(items, it)
                ui.notify("Deleted.", type="positive")
                render_fn()

            ui.button("Confirm delete", icon="delete", on_click=_on_delete).props(
                "flat dense no-caps color=negative"
            )

    # ── Keep (dismiss) ────────────────────────────────────────────────────
    def _render_keep(it, items, render_fn):
        with ui.card().classes("w-full p-3 gap-2").props("flat bordered"):

            async def _on_keep():
                drop_kg_review(items, it, dismiss=True)
                ui.notify("Kept; won't be flagged again.", type="info")
                render_fn()

            ui.button("Keep — it's fine", icon="check", on_click=_on_keep).props(
                "flat dense no-caps color=positive"
            )

    # Build the reason filter once (outside results) — options updated in render()
    reason_select = ui.select(
        ["all"],
        value="all",
        label="Reason:",
    ).classes("w-full q-mb-sm")
    reason_select.on_value_change(lambda e: _update_reason(e, reason_select))

    render()