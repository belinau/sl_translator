"""Document Aligner page — drives the doc-pair bilingual pipeline."""

from __future__ import annotations

import uuid
from pathlib import Path

from nicegui import app, run, ui


def _align_offthread(kg, en, sl, container, dry_run):
    kg.reload_if_changed()
    from translate_core.document_pair_pipeline import process_pair

    return process_pair(en, sl, container, kg, dry_run=dry_run)


def _container_candidates(kg) -> list[dict]:
    all_src = kg.get_all_by_type("source_text") or []
    cands = [
        s for s in all_src
        if s.get("kind") == "translated_work"
        and s.get("provenance") in {"cobiss_personal", "curator_extra"}
    ]
    cands.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return cands


def _prov_tag(s: dict) -> str:
    return "COBISS" if s.get("provenance") == "cobiss_personal" else "manual"


def _run_dir(side: str) -> Path:
    d = Path("data") / "aligner" / (uuid.uuid4().hex[:8])
    d.mkdir(parents=True, exist_ok=True)
    return d


# MIME types for accepted formats — Quasar validates against MIME, not extensions
_ACCEPT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
    "text/markdown,"
    "text/plain,"
    "application/pdf"
)


@ui.page("/aligner")
def page_aligner() -> None:
    import app_state
    from ui import settings as ui_settings
    from ui.components import busy_overlay

    kg = app_state.kg
    if kg is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.icon("hourglass_empty", size="48px").props("color=primary")
            ui.label("Backend still initializing — please reload in a moment.").classes("text-lg mt-2")
        return

    if app_state.apply_colors is not None:
        app_state.apply_colors()
    dm = ui_settings.install_dark_mode()
    ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")

    state = app.storage.user
    state.setdefault("aligner_en", None)
    state.setdefault("aligner_sl", None)
    state.setdefault("aligner_container", None)

    with ui.row().classes(
        "w-full px-8 py-6 items-center justify-between shadow-lg text-white"
    ).style("background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);"):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense color=white"
            ).tooltip("Back to home")
            with ui.column().classes("gap-0"):
                ui.label("Document Aligner").classes("text-xl font-black tracking-tighter")
                ui.label("Align an EN/SL pair → TMX + KG entities").classes(
                    "text-[10px] font-bold uppercase tracking-[0.2em] opacity-60"
                )
        ui_settings.dark_toggle_button(dm)

    async def handle_upload(e, side: str) -> None:
        name = e.file.name
        saved = _run_dir(side) / name
        state[f"aligner_{side}"] = str(saved)
        lbl = en_label if side == "en" else sl_label
        lbl.set_text(f"✓ {name}")
        lbl.classes(add="text-positive", remove="opacity-60")
        await e.file.save(saved)
        ui.notify(f"{side.upper()} source: {name}", type="positive")

    async def upload_en(e) -> None:
        await handle_upload(e, "en")

    async def upload_sl(e) -> None:
        await handle_upload(e, "sl")

    en_label = sl_label = None
    with ui.card().classes("w-full").props("flat bordered"):
        ui.label("Document pair").classes("text-h6 q-mb-sm")
        with ui.row().classes("w-full q-gutter-md"):
            with ui.column().classes("col"):
                ui.label("EN source (original)").classes("text-caption")
                ui.upload(on_upload=upload_en, auto_upload=True,
                          label="Drop EN file here or click to browse", max_files=1) \
                    .classes("w-full").props(f"color=accent accept={_ACCEPT} flat bordered")
                en_label = ui.label("EN: not set").classes("text-caption opacity-60")
                if state["aligner_en"]:
                    en_label.set_text(f"✓ {Path(state['aligner_en']).name}")
                    en_label.classes(add="text-positive", remove="opacity-60")
            with ui.column().classes("col"):
                ui.label("SL source (translation)").classes("text-caption")
                ui.upload(on_upload=upload_sl, auto_upload=True,
                          label="Drop SL file here or click to browse", max_files=1) \
                    .classes("w-full").props(f"color=accent accept={_ACCEPT} flat bordered")
                sl_label = ui.label("SL: not set").classes("text-caption opacity-60")
                if state["aligner_sl"]:
                    sl_label.set_text(f"✓ {Path(state['aligner_sl']).name}")
                    sl_label.classes(add="text-positive", remove="opacity-60")

    def open_new_container_dialog() -> None:
        from translate_core.cobiss_classifier import CONTAINER_TYPES
        from translate_core.entity_extraction._slug import _slugify
        from translate_core.entity_extraction.ingest_helpers import ensure_agent

        with ui.dialog() as dialog, ui.card().classes("min-w-[460px]"):
            ui.label("Add new container").classes("text-lg font-bold mb-2")
            ui.label(
                "For translations not in your COBISS bibliography (radio, "
                "podcasts, …). This becomes the cited_in target."
            ).classes("text-caption opacity-70 mb-3")
            title_input = ui.input("Title *").props("outlined dense").classes("w-full mb-2")
            ptype_select = ui.select(
                list(CONTAINER_TYPES), value="book_translation", label="Project type",
            ).props("outlined dense").classes("w-full mb-2")
            translator_input = ui.input(
                "Translator", value="Urban Belina",
            ).props("outlined dense").classes("w-full mb-2")
            year_input = ui.number("Year (optional)", format="%d").props("outlined dense").classes("w-full mb-3")

            def _create() -> None:
                title = (title_input.value or "").strip()
                if not title:
                    ui.notify("Title is required", type="warning")
                    return
                slug = _slugify(title)
                node_id = f"source:{slug}"
                ptype = ptype_select.value or "book_translation"
                translator_name = (translator_input.value or "Urban Belina").strip() or "Urban Belina"
                year_val = year_input.value
                year_int = int(year_val) if year_val not in (None, "") else None
                try:
                    if not kg.G.has_node(node_id):
                        kg.add_source_text_node(
                            slug, title, provenance="curator_extra",
                            kind="translated_work", project_type=ptype, year=year_int,
                        )
                        t_slug = ensure_agent(kg, translator_name, role="translator")
                        kg.link_translated_by(slug, t_slug)
                        kg.save()
                        ui.notify(f"Created container: {slug}", type="positive")
                    else:
                        nd = kg.G.nodes[node_id]
                        nd.setdefault("kind", "translated_work")
                        nd.setdefault("provenance", "curator_extra")
                        nd.setdefault("project_type", ptype)
                        ui.notify(f"Container already exists — selecting {slug}", type="info")
                    state["aligner_container"] = node_id
                    container_select.set_options(_container_opts(), value=node_id)
                except Exception as ex:  # noqa: BLE001
                    ui.notify(f"Failed to create container: {ex}", type="negative")
                    return
                dialog.close()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: dialog.close()).props("flat")
                ui.button("Create", on_click=_create).props("color=positive unelevated")
        dialog.open()

    def _container_opts() -> dict:
        return {
            s["id"]: f'[{_prov_tag(s)}] {s.get("title", "(untitled)")} — {s["id"]}'
            for s in _container_candidates(kg)
        }

    with ui.card().classes("w-full").props("flat bordered"):
        ui.label("Container work (your translated publication)").classes("text-h6 q-mb-sm")
        ui.label(
            "Pick the publication that contains these citations. Type to filter. "
            "For translations COBISS doesn't cover (radio, podcasts, …) click "
            "\u201cAdd new container\u201d."
        ).classes("text-caption opacity-70 q-mb-sm")
        with ui.row().classes("w-full q-gutter-md items-center"):
            container_select = ui.select(
                _container_opts(), with_input=True, label="Container",
                value=state.get("aligner_container"),
                on_change=lambda e: state.__setitem__("aligner_container", e.value),
            ).classes("col-grow")
            ui.button(
                "Add new container", icon="add_circle", on_click=open_new_container_dialog,
            ).props("outline dense color=primary").tooltip(
                "Create a container for a translation COBISS doesn't list."
            )

    async def do_run(dry_run: bool) -> None:
        en, sl, container = state["aligner_en"], state["aligner_sl"], state["aligner_container"]
        missing = [
            lbl for lbl, val in (
                ("EN source", en), ("SL source", sl), ("container", container),
            ) if not val
        ]
        if missing:
            ui.notify(f"Select {', '.join(missing)} first", type="warning")
            return
        try:
            async with busy_overlay("Aligning…" if dry_run else "Aligning & writing to KG…"):
                res = await run.io_bound(_align_offthread, kg, Path(en), Path(sl), container, dry_run)
                if not dry_run:
                    await run.io_bound(kg.save)
            summary = (
                f"body={res.n_body_pairs} bilingual={res.n_bilingual} "
                f"en_only={res.n_en_only} sl_only={res.n_sl_only}"
            )
            if dry_run:
                ui.notify(f"Preview: {summary}", type="info")
            else:
                ir = getattr(res, "ingest_report", None)
                if ir is not None:
                    summary += (
                        f" · written={getattr(ir, 'written', 0)} "
                        f"queued={getattr(ir, 'queued', 0)} "
                        f"dropped={getattr(ir, 'dropped', 0)}"
                    )
                ui.notify(f"KG saved — {summary}", type="positive")
        except Exception as ex:  # noqa: BLE001
            ui.notify(f"Alignment failed: {ex}", type="negative")

    with ui.row().classes("q-gutter-md q-mt-sm"):
        ui.button("Preview (dry run)", icon="visibility", on_click=lambda: do_run(True)).props(
            "unelevated color=primary"
        )
        ui.button("Process & write to KG", icon="compare_arrows", on_click=lambda: do_run(False)).props(
            "unelevated color=positive"
        )


__all__ = ["page_aligner"]
