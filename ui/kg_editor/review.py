"""KG editor — Extraction Review page. NiceGUI port of kg_editor_ui.py page_review (lines 918–1080)."""

from __future__ import annotations

import json
from collections import Counter

from nicegui import ui, run

from translate_core.kg_review_ops import (
    REVIEW_PATH,
    PREVIEW_PATH,
    RECLASS_TARGETS,
    record_matches_text,
    record_label,
    commit_record,
    candidate_texts,
    commit_as,
    drop_from_queue,
)

PAGE_SIZE = 10


@ui.page("/kg/review")
def page_review():
    from ui.kg_editor.common import kg_frame, build_reclass_inputs, render_record

    kg, _glossary = kg_frame("/kg/review")
    if kg is None:
        return

    # ── Mutable state closures (survive render() clear) ────────────────────
    kind_filter_ref: dict = {"value": "all"}
    min_conf_ref: dict = {"value": 0.55}
    text_filter_ref: dict = {"value": ""}
    page_ref: dict = {"value": 1}

    results = ui.column().classes("w-full")

    # ── Filter row (built once, outside results) ────────────────────────────
    with ui.row().classes("w-full items-center q-gutter-sm q-mb-sm"):
        kind_select = ui.select(
            {"all": "all (0)"},
            value="all",
            label="Kind",
        ).classes("col-3")
        kind_select.on_value_change(lambda e: _update_filter("kind", e.value, page_reset=True))

        min_conf_slider = ui.number(
            "Min confidence",
            value=0.55,
            min=0.0,
            max=1.0,
            step=0.05,
            format="%.2f",
        ).classes("col-3")
        min_conf_slider.on_value_change(lambda e: _update_filter("min_conf", e.value, page_reset=True))

        text_input = ui.input(
            "Filter by text",
            value="",
            placeholder="author/title/name",
        ).props("outlined dense clearable debounce=300").classes("col-4")
        text_input.on_value_change(lambda e: _update_filter("text", e.value, page_reset=True))

    # ── Main render ─────────────────────────────────────────────────────────
    def render():
        results.clear()
        with results:
            # ── Header metrics ──────────────────────────────────────────
            with ui.row().classes("w-full items-center q-gutter-md q-mb-md"):
                if PREVIEW_PATH.exists():
                    size_kb = PREVIEW_PATH.stat().st_size // 1024
                    ui.label(f"Preview: {size_kb} KB").classes("text-caption")
                else:
                    ui.label(
                        "No preview yet — run extractor --dry-run --preview-patterns."
                    ).classes("text-caption text-grey-6")

                if REVIEW_PATH.exists():
                    size_kb = REVIEW_PATH.stat().st_size // 1024
                    ui.label(f"Review queue: {size_kb} KB").classes("text-caption")

                ui.button(icon="refresh", on_click=lambda: render()).props(
                    "flat round dense size=sm"
                ).tooltip("Reload review queue")

            # ── Load queue ──────────────────────────────────────────────
            if not REVIEW_PATH.exists():
                ui.label(
                    "No extraction_review.json found. Run the extractor first."
                ).classes("text-negative")
                return

            try:
                review_records = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                ui.label(f"Could not load review queue: {exc}").classes("text-negative")
                return

            if not review_records:
                ui.label("Review queue is empty — nothing pending.").classes("text-positive")
                return

            # ── Filters ─────────────────────────────────────────────────
            kinds_present = Counter(r["kind"] for r in review_records)
            kind_choices = sorted(kinds_present.keys())

            kind_opts = {"all": f"all ({len(review_records)})"}
            kind_opts.update(
                {k: f"{k} ({kinds_present.get(k, 0)})" for k in kind_choices}
            )
            kind_select.set_options(kind_opts)

            current_kind = kind_filter_ref["value"]
            current_min_conf = min_conf_ref["value"]
            current_text = text_filter_ref["value"]

            # ── Apply filters ────────────────────────────────────────────
            filtered = [
                r
                for r in review_records
                if (current_kind == "all" or r["kind"] == current_kind)
                and r.get("confidence", 0) >= current_min_conf
                and (not current_text or record_matches_text(r, current_text))
            ]

            ui.label(
                f"{len(filtered)} of {len(review_records)} records match filters."
            ).classes("text-caption q-mb-sm")

            # ── Bulk accept ──────────────────────────────────────────────
            with ui.expansion(
                "Bulk accept (use with care)", icon="rocket_launch"
            ).classes("w-full q-mb-sm"):
                bulk_kind_select = ui.select(
                    ["(pick a kind)"] + kind_choices,
                    value="(pick a kind)",
                    label="Accept all records of kind",
                ).classes("w-full q-mb-sm")

                bulk_min_conf = ui.number(
                    "Minimum confidence for bulk accept:",
                    value=0.70,
                    min=0.55,
                    max=1.0,
                    step=0.05,
                    format="%.2f",
                ).classes("w-full q-mb-sm")

                async def _bulk_accept():
                    bk = bulk_kind_select.value
                    if bk == "(pick a kind)":
                        ui.notify("Pick a kind first.", type="warning")
                        return
                    accepted = 0
                    remaining = []
                    for r in review_records:
                        if r["kind"] == bk and r.get("confidence", 0) >= bulk_min_conf.value:
                            commit_record(kg, r)
                            accepted += 1
                        else:
                            remaining.append(r)
                    content = json.dumps(remaining, ensure_ascii=False, indent=2, default=str)
                    await run.io_bound(
                        REVIEW_PATH.write_text, content, encoding="utf-8"
                    )
                    await run.io_bound(kg.save)
                    ui.notify(f"Committed {accepted} records. KG saved.", type="positive")
                    render()

                ui.button(
                    "Accept all matching", on_click=_bulk_accept
                ).props("color=primary").classes("w-full")

            # ── Bulk reject ──────────────────────────────────────────────
            with ui.expansion(
                "Bulk reject (remove filtered)", icon="delete_sweep"
            ).classes("w-full q-mb-md"):
                ui.label(
                    f"This will permanently remove all {len(filtered)} records currently "
                    "matching the filter from the review queue. They will NOT be committed "
                    "to the KG."
                ).classes("text-caption q-mb-sm")

                # Snapshot for closure — filtered list identity at render time
                _filtered_snapshot = filtered
                _filtered_count = len(filtered)

                confirm_input = ui.input(
                    "Type the count to confirm:",
                    placeholder=str(_filtered_count),
                ).classes("w-full q-mb-sm")

                async def _bulk_reject():
                    if confirm_input.value != str(_filtered_count):
                        ui.notify(
                            f"Confirmation count mismatch — type {_filtered_count} to proceed.",
                            type="negative",
                        )
                        return
                    if not _filtered_snapshot:
                        return
                    filtered_ids = {id(r) for r in _filtered_snapshot}
                    remaining = [r for r in review_records if id(r) not in filtered_ids]
                    content = json.dumps(remaining, ensure_ascii=False, indent=2, default=str)
                    await run.io_bound(
                        REVIEW_PATH.write_text, content, encoding="utf-8"
                    )
                    ui.notify(f"Discarded {_filtered_count} records.", type="info")
                    render()

                ui.button("Discard filtered records", on_click=_bulk_reject).props(
                    "color=negative outline"
                ).classes("w-full")

            # ── Pagination ───────────────────────────────────────────────
            total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page = min(page_ref["value"], total_pages)
            page_ref["value"] = current_page
            page_start = (current_page - 1) * PAGE_SIZE
            page_slice = filtered[page_start : page_start + PAGE_SIZE]

            if total_pages > 1:
                ui.pagination(
                    1,
                    total_pages,
                    direction_links=True,
                    value=current_page,
                    on_change=lambda e: _update_page(e.value),
                ).classes("q-mb-md")

            # ── Per-record expansions ────────────────────────────────────
            label_to_target = {v: k for k, v in RECLASS_TARGETS.items()}

            for r in page_slice:
                lbl = record_label(r)
                with ui.expansion(
                    f"{r['kind']} — {lbl}",
                    caption=f"conf {r.get('confidence', 0):.2f}",
                    icon="description",
                ).classes("w-full"):
                    render_record(r)
                    ui.separator().classes("q-my-sm")

                    options = [f"(keep as) {r['kind']}"] + list(RECLASS_TARGETS.values())
                    choice_select = ui.select(
                        options,
                        value=options[0],
                        label="Commit as",
                    ).classes("w-full q-mb-sm")
                    choice_select.tooltip(
                        "Keep the original kind, or reclassify this record into a "
                        "different entity type before committing."
                    )

                    # Container that switches between keep-as and reclass
                    action_col = ui.column().classes("w-full")

                    def _build_actions(
                        _r=r,
                        _choice_select=choice_select,
                        _label_to_target=label_to_target,
                        _review_records=review_records,
                        _action_col=action_col,
                        _choice_options=options,
                    ):
                        _action_col.clear()
                        with _action_col:
                            choice = _choice_select.value
                            if choice.startswith("(keep as)"):
                                ui.label(
                                    "Commits using the record's original fields."
                                ).classes("text-caption q-mb-xs")
                                with ui.row().classes("q-gutter-sm"):
                                    async def _accept(_r=_r, _rr=_review_records):
                                        commit_record(kg, _r)
                                        await run.io_bound(kg.save)
                                        drop_from_queue(_rr, _r)
                                        ui.notify("Committed.", type="positive")
                                        render()

                                    ui.button("Accept → KG", on_click=_accept, icon="check").props(
                                        "color=positive"
                                    )

                                    async def _reject(_r=_r, _rr=_review_records):
                                        drop_from_queue(_rr, _r)
                                        ui.notify("Removed from queue.", type="info")
                                        render()

                                    ui.button("Reject", on_click=_reject, icon="delete").props(
                                        "color=negative outline"
                                    )
                            else:
                                target = _label_to_target.get(choice, choice)
                                cands = candidate_texts(_r)
                                ui.label(
                                    f"Reclassify → {choice}. Confirm the fields:"
                                ).classes("text-caption q-mb-xs")
                                field_els = build_reclass_inputs(target, cands)

                                async def _commit_reclass(
                                    _r=_r,
                                    _target=target,
                                    _field_els=field_els,
                                    _rr=_review_records,
                                    _choice=choice,
                                ):
                                    fields = {k: el.value for k, el in _field_els.items()}
                                    err, _new_id = commit_as(kg, _target, fields)
                                    if err:
                                        ui.notify(err, type="negative")
                                        return
                                    await run.io_bound(kg.save)
                                    drop_from_queue(_rr, _r)
                                    ui.notify(f"Committed as {_choice}.", type="positive")
                                    render()

                                ui.button(
                                    f"Commit as {choice}", on_click=_commit_reclass, icon="check"
                                ).props("color=positive").classes("q-mt-sm")

                                async def _reject_reclass(_r=_r, _rr=_review_records):
                                    drop_from_queue(_rr, _r)
                                    ui.notify("Removed from queue.", type="info")
                                    render()

                                ui.button("Reject", on_click=_reject_reclass, icon="delete").props(
                                    "color=negative outline"
                                )

                    # Build initial actions and react to choice change
                    _build_actions()
                    choice_select.on_value_change(lambda e: _build_actions())

    # ── Filter helpers ──────────────────────────────────────────────────
    def _update_filter(field: str, value, *, page_reset: bool = False):
        if field == "kind":
            kind_filter_ref["value"] = value
        elif field == "min_conf":
            min_conf_ref["value"] = value
        elif field == "text":
            text_filter_ref["value"] = (value or "").strip().lower()
        if page_reset:
            page_ref["value"] = 1
        render()

    def _update_page(new_page):
        page_ref["value"] = new_page
        render()

    # ── Initial render ───────────────────────────────────────────────────
    render()