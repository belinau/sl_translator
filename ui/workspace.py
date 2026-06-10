"""@ui.page('/translate/{project_id}') — the rebuilt translation workspace.

Persistent DOM, virtualized navigator, client-side ghost predictions,
non-blocking confirm. Top bar exposes dark mode toggle.

Module-level imports are kept minimal so that this file can be imported by
main.py without triggering circular imports at module load time. All
resource globals are looked up lazily inside page_translate via `from main
import ...`.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

from nicegui import background_tasks, ui

from . import intel_panel, kg_search, predictions, segment_editor, segment_navigator, settings as ui_settings
from .state import WorkspaceState, request_kg_save


# SHARED_CSS (in ui/settings.py) already contains all structural styling.
# No duplicate page-style block here.


@ui.page("/translate/{project_id}")
def page_translate(project_id: str):
    # Read live resource references from the shared state module. Using a
    # dedicated module instead of sys.modules['__main__'] works regardless of
    # whether main.py was launched as __main__ or as a uvicorn worker under
    # __mp_main__ (NiceGUI's auto-reload spawns the latter).
    import app_state
    PROJECTS_DIR = app_state.PROJECTS_DIR
    apply_colors = app_state.apply_colors
    config = app_state.config
    doc_parser = app_state.doc_parser
    glossary = app_state.glossary
    kg = app_state.kg
    load_project = app_state.load_project
    parse_lang_pair = app_state.parse_lang_pair
    qa_engine = app_state.qa_engine
    save_pair_to_tm = app_state.save_pair_to_tm
    save_project = app_state.save_project
    tm = app_state.tm

    # If init_resources hasn't completed yet (page hit during startup window),
    # tell the user instead of feeding None into the rest of the page.
    if any(x is None for x in (tm, glossary, kg, qa_engine, doc_parser,
                                 PROJECTS_DIR, apply_colors, load_project,
                                 save_project, save_pair_to_tm, parse_lang_pair, config)):
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.icon("hourglass_empty", size="48px").props("color=primary")
            ui.label("Backend still initializing — please reload in a moment.").classes("text-lg mt-2")
        return

    # Type-narrow for the remainder of this function. The any-None guard above
    # already returned, so every reference is non-Optional from here on.
    assert (
        PROJECTS_DIR is not None and apply_colors is not None and config is not None
        and doc_parser is not None and glossary is not None and kg is not None
        and load_project is not None
        and parse_lang_pair is not None and qa_engine is not None
        and save_pair_to_tm is not None and save_project is not None
        and tm is not None
    )

    apply_colors()
    # Minimal CSS for the ghost-text dual-layer technique only. Everything else
    # is NiceGUI components letting Quasar handle dark/light styling natively.
    ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")
    predictions.inject_runtime()

    dm = ui_settings.install_dark_mode()

    data = load_project(project_id)
    if data is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.label("Project not found.").classes("text-red-500 text-lg")
            ui.button("← Back to projects", on_click=lambda: ui.navigate.to("/")).classes("mt-4")
        return

    # Persistence layer stores 'id' but WorkspaceState wants 'project_id'.
    # Normalize and inject the URL parameter as the canonical id.
    ws_dict = dict(data)
    ws_dict["project_id"] = project_id
    # Capture the NiceGUI Client during page construction so background tasks
    # can safely enter `with state.client:` for any UI-side mutation that fires
    # outside the original request scope.
    page_client = ui.context.client
    state = WorkspaceState(ws_dict, save_callback=save_project, client=page_client)

    deps = {
        "tm": tm,
        "glossary": glossary,
        "kg": kg,
        "qa_engine": qa_engine,
        "parse_lang_pair": parse_lang_pair,
    }

    # ------------------------------------------------------------------
    # Top bar
    # ------------------------------------------------------------------
    progress_value = {"value": state.progress()}

    # Top bar — flat-bordered QCard auto-darks. No color classes needed.
    with ui.card().classes(
        "w-full flex-row items-center justify-between rounded-none px-5 py-3"
    ).props("flat bordered"):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense"
            ).tooltip("Back to projects")
            with ui.column().classes("gap-0"):
                ui.label(state.filename).classes("text-sm font-bold leading-none")
                ui.label(state.lang_pair).classes("text-[10px] opacity-60")
            ui.button(
                icon="menu_book",
                on_click=lambda: _open_glossary(state, glossary, config, parse_lang_pair, kg),
            ).props("flat round dense size=sm color=grey-6").tooltip("Add Glossary Term")

        with ui.column().classes("w-44 items-center gap-0.5"):
            progress_pct_label = ui.label(f"{int(state.progress() * 100)}%").classes(
                "text-[10px] font-bold uppercase tracking-widest opacity-70"
            )
            progress_bar = ui.linear_progress(
                value=state.progress(), color="positive"
            ).props('size="6px" :show-value="false"').classes("w-full rounded-full")

        with ui.row().classes("gap-2 items-center"):
            ui_settings.dark_toggle_button(dm)

            with ui.dropdown_button("Export", icon="file_download", auto_close=True).props(
                "rounded unelevated dense color=positive"
            ):
                ui.item("Translated Book (.docx)", on_click=lambda: _export_target_docx())
                ui.item("Reorganized Source (.docx)", on_click=lambda: _export_source_docx())
                ui.item("Plain .txt", on_click=lambda: _export_txt())

    def _update_progress():
        p = state.progress()
        progress_value["value"] = p
        try:
            progress_bar.set_value(p)
            progress_pct_label.set_text(f"{int(p * 100)}%")
        except Exception:
            pass

    state.subscribe("segments", _update_progress)

    # ------------------------------------------------------------------
    # Chapter outline sidebar (from VL pipeline, if available)
    # ------------------------------------------------------------------
    outline_entries = []
    project_data = load_project(state.project_id)
    if project_data and "outline" in project_data:
        outline_entries = project_data["outline"].get("entries", [])

    with (
        ui.right_drawer(value=True, fixed=True)
        .props("width=380 bordered")):
        with ui.column().classes("w-full p-4 gap-2"):
            # Chapter outline (VL-generated books only)
            if outline_entries:
                # Build chapter -> segment_index map from segments_meta.
                # segments_meta[i] corresponds to segments[i] (same order, same length).
                # We map each chapter_index to the first segment that belongs to it.
                chapter_to_seg = {}  # chapter_index -> first segment id
                if project_data and "segments_meta" in project_data:
                    for i, sm in enumerate(project_data["segments_meta"]):
                        ch_idx = sm.get("chapter_index", 0)
                        if ch_idx not in chapter_to_seg:
                            chapter_to_seg[ch_idx] = i  # segment id = index in segments list

                toc_chapters = [e for e in outline_entries if e.get("kind") in ("chapter", "part", "front_matter")]

                with ui.expansion("Chapters", icon="menu_book").classes(
                    "w-full"
                ).props("dense").classes("mb-2"):
                    for entry in outline_entries:
                        indent = entry.get("level", 1)
                        kind = entry.get("kind", "chapter")
                        number = entry.get("number", "")
                        title = entry.get("title", "Untitled")
                        prefix = f"{number}. " if number else ""
                        icon_name = {
                            "part": "bookmark",
                            "front_matter": "article",
                            "back_matter": "attachment",
                        }.get(kind, "description")
                        # Find the chapter index for this entry to enable click-to-navigate
                        entry_ch_idx = None
                        for ci, ch in enumerate(toc_chapters):
                            if ch is entry:
                                entry_ch_idx = ci
                                break
                        target_seg = chapter_to_seg.get(entry_ch_idx, 0) if entry_ch_idx is not None else 0

                        with ui.row().classes(
                            f"pl-{indent * 2} items-center gap-1 cursor-pointer hover:bg-blue-50 dark:hover:bg-slate-700 rounded"
                        ).on(
                            "click",
                            handler=lambda idx=target_seg: state.set_active(int(idx)),
                        ):
                            ui.icon(icon_name, size="14px").classes("opacity-50")
                            ui.label(f"{prefix}{title}").classes(
                                "text-[11px] font-medium truncate"
                            )

            segment_navigator.build(state)
            kg_search.build(state, deps)

    with ui.column().classes("w-full h-screen pt-2 overflow-hidden no-wrap"):
        with ui.column().classes("w-full flex-1 overflow-y-auto pb-8"):
            with ui.column().classes("w-full max-w-4xl mx-auto px-4"):
                def _trigger_confirm() -> None:
                    background_tasks.create(_confirm_segment(), name="confirm_btn")

                intel_panel.build(state, deps)
                segment_editor.build(state, deps, on_confirm=_trigger_confirm)


    # ------------------------------------------------------------------
    # Confirm + batch + KG/TM promotion
    # ------------------------------------------------------------------
    # Segment types excluded from KG promotion (noun-chunk noise) — they
    # still save to TM; whole-bibliography pages are handled by the
    # ingest_book_bibliography.py CLI.
    _KG_SKIP_TYPES = {"bibliography", "index"}

    async def _confirm_segment():
        if not state.segments:
            return
        idx = state.active_index
        seg = state.segments[idx]
        # The editor textarea binds to state.current["target"]; sync the latest
        # edited text onto the segment before validating / promoting.
        seg["target"] = state.current.get("target", seg.get("target", ""))
        if not seg["target"].strip():
            return
        state.mark_done(idx)
        next_idx = idx + 1
        if next_idx < len(state.segments):
            state.set_active(next_idx)
        else:
            with state.client:
                ui.notify("Document complete! 🎉", type="positive")
        background_tasks.create(_promote_pair(seg, state.lang_pair, idx), name="kg_promote")

    async def _promote_pair(seg: dict, lang_pair: str, seg_index: int = -1):
        src, tgt = parse_lang_pair(lang_pair)
        loop = asyncio.get_running_loop()

        # Phase 2 KG filter: skip promote_pair for bibliography/index segments
        # if segments_meta is available. These types produce noisy noun chunks
        # that pollute concept extraction.
        seg_meta = None
        project_data = load_project(state.project_id)
        if project_data and "segments_meta" in project_data:
            meta_list = project_data["segments_meta"]
            if 0 <= seg_index < len(meta_list):
                seg_meta = meta_list[seg_index]

        if seg_meta and seg_meta.get("type") in _KG_SKIP_TYPES:
            # Still save to TM, but skip KG promotion
            try:
                await loop.run_in_executor(
                    None,
                    lambda: save_pair_to_tm(seg["source"], seg["target"], lang_pair),
                )
            except Exception as e:
                log.warning(f"promote_pair TM-only: {e}")
            return

        # Derive domain and context from segments_meta for KG enrichment
        domain = ""
        context_text = seg["source"]
        if seg_meta:
            domain = seg_meta.get("outline_path", "")
            # For chapter_title segments, use the title itself as domain
            if seg_meta.get("type") == "chapter_title" and not domain:
                domain = seg["source"].strip().lstrip("# ").strip()

        # Project container slug — same slugification as the glossary
        # dialog (see _kg_add): NFKD-ish collapse of filename or
        # project_id into a "source:<slug>" id.
        import re as _re
        _base = (state.filename or state.project_id or "project").lower()
        _proj_slug = (_re.sub(r"[^a-z0-9]+", "-", _base).strip("-")[:80]) or "project"
        try:
            if kg is not None:
                # Pick up any external KG edits (maintenance scripts) before we
                # promote + save, so the editor's save merges on top instead of
                # clobbering them.
                await loop.run_in_executor(None, kg.reload_if_changed)
                await loop.run_in_executor(
                    None,
                    lambda: kg.promote_pair(
                        seg["source"], seg["target"], src, tgt,
                        verified=True, domain=domain, context=context_text,
                        source_text_id=_proj_slug,
                        agent_id="urban-belina",
                    ),
                )

            await loop.run_in_executor(
                None,
                lambda: save_pair_to_tm(seg["source"], seg["target"], lang_pair),
            )
            if kg is not None:
                request_kg_save(kg.save, delay=3.0)

            # Live smol entity extraction: every confirmed segment goes
            # through Ollama (glm) → ontology record builders → the O-10
            # confidence-tier chokepoint. If Ollama is unreachable the
            # segment stays in working.tmx for the offline batch pipeline.
            if kg is not None and getattr(config, "SMOL_LIVE_EXTRACTION", False):
                try:
                    from translate_core.citation_collector import (
                        collect_from_editor_segment,
                        extract_and_ingest,
                    )
                    snippet = collect_from_editor_segment(
                        segment_text=seg["source"],
                        segments_meta_entry=seg_meta or {},
                        project_id=state.project_id,
                        container_work_id=_proj_slug,
                        target_text=seg["target"],
                        lang_pair=f"{src}-{tgt}",
                    )
                    if snippet is not None:
                        report = await loop.run_in_executor(
                            None,
                            lambda: extract_and_ingest([snippet], kg),
                        )
                        if report.written or report.queued:
                            with state.client:
                                ui.notify(
                                    f"Entities: {report.written} written, "
                                    f"{report.queued} queued for review",
                                    type="positive",
                                )
                        request_kg_save(kg.save, delay=1.0)
                except Exception as e:
                    log.warning(f"promote_pair entity extraction: {e}")

        except Exception as e:
            log.error(f"promote_pair: {e}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _compile_md(use_target: bool) -> str:
        lines = []
        for s in state.segments:
            if use_target and s["target"].strip():
                lines.append(s["target"].strip())
            else:
                lines.append(s["source"].strip())
        return "\n\n".join(lines)

    def _export_target_docx():
        # Template-based export: clone original DOCX, replace translated
        # segments in-place to preserve all formatting and structure.
        template = PROJECTS_DIR / f"{state.project_id}.docx"
        if template.exists() and any(
            "docx_para_idx" in s for s in state.segments
        ):
            out = PROJECTS_DIR / f"compiled_target_{state.project_id}.docx"
            try:
                doc_parser.compile_from_template(
                    template, out, state.segments,
                )
                if out.exists():
                    ui.download(out.read_bytes(), f"translated_{state.filename}")
                    out.unlink(missing_ok=True)
                    return
            except Exception as e:
                log.error(f"template export: {e}")
        # Fallback: markdown compilation (no paragraph index mapping).
        md = _compile_md(use_target=True)
        path = PROJECTS_DIR / f"compiled_target_{state.project_id}.docx"
        try:
            doc_parser.compile_to_designed_docx(md, path)
            if path.exists():
                ui.download(path.read_bytes(), f"translated_{state.filename}")
                path.unlink(missing_ok=True)
                return
        except Exception as e:
            log.error(f"export target: {e}")
        ui.notify("Export failed", type="negative")

    def _export_source_docx():
        md = _compile_md(use_target=False)
        path = PROJECTS_DIR / f"compiled_source_{state.project_id}.docx"
        try:
            doc_parser.compile_to_designed_docx(md, path)
            if path.exists():
                ui.download(path.read_bytes(), f"reorganized_source_{state.filename}")
                path.unlink(missing_ok=True)
                return
        except Exception as e:
            log.error(f"export source: {e}")
        ui.notify("Export failed", type="negative")

    def _export_txt():
        content = _compile_md(use_target=True)
        ui.download(content.encode("utf-8"), f"translated_{state.filename}.txt")

    # ------------------------------------------------------------------
    # Keyboard chords
    # ------------------------------------------------------------------
    def _on_key(e):
        try:
            key = (e.key.name or "").lower()
        except Exception:
            return
        mod = e.modifiers.meta or e.modifiers.ctrl
        if not e.action.keydown:
            return
        if mod and key == "enter":
            background_tasks.create(_confirm_segment(), name="confirm_chord")
        elif mod and key == "arrowup":
            state.set_active(state.active_index - 1)
        elif mod and key == "arrowdown":
            state.set_active(state.active_index + 1)

    ui.keyboard(on_key=_on_key, ignore=["input", "select", "button"])


# ---------------------------------------------------------------------------
# Glossary dialog (module-level for cleanliness; called from top bar)
# ---------------------------------------------------------------------------
def _open_glossary(state: WorkspaceState, glossary, config, parse_lang_pair, kg=None):
    with ui.dialog() as dialog, ui.card().classes("min-w-[400px]"):
        ui.label("Add to Glossary").classes("text-lg font-bold mb-2")
        src_lang, tgt_lang = parse_lang_pair(state.lang_pair)
        src_input = ui.input(f"Source Term ({src_lang})").classes("w-full")
        tgt_input = ui.input(f"Target Term ({tgt_lang})").classes("w-full")
        note_input = ui.input("Note (optional)").classes("w-full")
        # Searchable dropdown of existing lineages + type-to-add, so the same
        # theoretical lineage is reused (no free-text variant proliferation).
        try:
            _existing_lineages = kg.get_all_lineages() if kg is not None else []
        except Exception:
            _existing_lineages = []
        lineage_input = ui.select(
            options=_existing_lineages,
            label="Theoretical lineage (pick existing or type to add)",
            with_input=True,
            new_value_mode="add-unique",
        ).classes("w-full")

        def _save():
            s = (src_input.value or "").strip()
            t = (tgt_input.value or "").strip()
            if not (s and t):
                ui.notify("Both terms are required", type="negative")
                return
            try:
                config.GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
                custom_path = config.GLOSSARY_DIR / "custom.tsv"
                line = f"{s}\t{t}\t{note_input.value or ''}\n"
                with open(custom_path, "a", encoding="utf-8") as f:
                    f.write(line)
                if glossary is not None:
                    glossary._add_simple_entry(
                        s, t, src_lang, tgt_lang, "custom.tsv", note_input.value or ""
                    )
                    glossary._build_indices()
                # Also push the term to the KG, tagged with the current project
                # (instantiated_in -> project source_text) so the mapping carries
                # "made while translating this book" provenance. Done off the UI
                # thread; reload_if_changed keeps it from clobbering external edits.
                if kg is not None:
                    import re as _re
                    note_val = note_input.value or ""
                    lineage_val = (lineage_input.value or "").strip() or "general"
                    proj_title = state.filename or "current project"
                    base = (state.filename or state.project_id or "project").lower()
                    proj_slug = (_re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:80]) or "project"

                    async def _kg_add():
                        loop = asyncio.get_running_loop()

                        def _do():
                            kg.reload_if_changed()
                            if not kg.G.has_node(f"source:{proj_slug}"):
                                kg.add_source_text_node(
                                    proj_slug, title=proj_title,
                                    project_type="book_translation",
                                )
                            sid = kg.add_term_node(s, src_lang, is_phrase=" " in s)
                            tid = kg.add_term_node(t, tgt_lang, is_phrase=" " in t)
                            kg.link_translations_with_context(
                                src_term_id=sid, tgt_term_id=tid, confidence=1.0,
                                lineage=lineage_val, gloss=note_val, verified=True,
                                source_text_id=proj_slug,
                            )
                            kg.save()
                        await loop.run_in_executor(None, _do)

                    background_tasks.create(_kg_add(), name="glossary_kg")
                ui.notify("Term added to glossary", type="positive")
                dialog.close()
            except Exception as ex:
                ui.notify(f"Error: {ex}", type="negative")

        with ui.row().classes("w-full justify-end mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_save).props("color=positive")
    dialog.open()
