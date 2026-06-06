"""@ui.page('/translate/{project_id}') — the rebuilt translation workspace.

Persistent DOM, virtualized navigator, client-side ghost predictions,
non-blocking confirm. Top bar exposes dark mode + AI pretranslate toggles.

Module-level imports are kept minimal so that this file can be imported by
main.py without triggering circular imports at module load time. All
resource globals are looked up lazily inside page_translate via `from main
import ...`.
"""
from __future__ import annotations

import asyncio

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
    llm_executor = app_state.llm_executor
    load_project = app_state.load_project
    parse_lang_pair = app_state.parse_lang_pair
    qa_engine = app_state.qa_engine
    save_pair_to_tm = app_state.save_pair_to_tm
    save_project = app_state.save_project
    tm = app_state.tm
    translator = app_state.translator

    # If init_resources hasn't completed yet (page hit during startup window),
    # tell the user instead of feeding None into the rest of the page.
    if any(x is None for x in (tm, glossary, kg, translator, qa_engine, doc_parser,
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
        and llm_executor is not None and load_project is not None
        and parse_lang_pair is not None and qa_engine is not None
        and save_pair_to_tm is not None and save_project is not None
        and tm is not None and translator is not None
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
        "translator": translator,
        "tm": tm,
        "glossary": glossary,
        "kg": kg,
        "qa_engine": qa_engine,
        "llm_executor": llm_executor,
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
            ai_master_on = ui_settings.ai_master_enabled()
            if ai_master_on:
                ai_switch = ui.switch(
                    "AI auto-draft",
                    value=ui_settings.ai_pretranslate_enabled(),
                    on_change=lambda e: ui_settings.set_ai_pretranslate(bool(e.value)),
                ).props("dense").classes("text-[11px]")
            else:
                ai_switch = ui.switch(
                    "AI auto-draft",
                    value=False,
                    on_change=lambda e: ui_settings.set_ai_pretranslate(bool(e.value)),
                ).props("dense disable").classes("text-[11px]").tooltip(
                    "AI Translation is disabled on the home page"
                )
            ui_settings.dark_toggle_button(dm)

            batch_btn_holder = ui.row().classes("items-center")

            def _render_batch_button():
                batch_btn_holder.clear()
                with batch_btn_holder:
                    if state.is_batch:
                        ui.button("Stop", icon="stop", on_click=_stop_batch).props(
                            "outline rounded dense color=negative"
                        )
                    elif not ai_master_on:
                        ui.button("Auto-translate", icon="auto_awesome").props(
                            "outline rounded dense color=grey disable"
                        ).tooltip("AI Translation is disabled on the home page")
                    else:
                        ui.button("Auto-translate", icon="auto_awesome", on_click=lambda: background_tasks.create(_batch())).props(
                            "outline rounded dense color=accent"
                        )

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
        .props("width=380 bordered") as intel_drawer
    ):
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

            nav_refs = segment_navigator.build(state)
            search_refs = kg_search.build(state, deps)

    with ui.column().classes("w-full h-screen pt-2 overflow-hidden no-wrap"):
        with ui.column().classes("w-full flex-1 overflow-y-auto pb-8"):
            with ui.column().classes("w-full max-w-4xl mx-auto px-4"):
                def _trigger_confirm() -> None:
                    background_tasks.create(_confirm_segment(), name="confirm_btn")

                editor_refs = segment_editor.build(state, deps, on_confirm=_trigger_confirm)
                intel_refs = intel_panel.build(state, deps)

    _render_batch_button()

    # ------------------------------------------------------------------
    # Confirm + batch + KG/TM promotion
    # ------------------------------------------------------------------
    # Segment types that need implementation of metadata to entities for KG.
    _KG_SKIP_TYPES = {"bibliography", "index"}
    _CITATION_TYPES = {"footnote", "endnote", "bibliography_entry"}

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
                print(f"[promote_pair TM-only] {e}")
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

            # Phase 7: Extract citations from footnote/bibliography segments
            # and ingest into KG via the typed pipeline.
            if seg_meta and seg_meta.get("type") in _CITATION_TYPES:
                try:
                    from translate_core.citation_collector import (
                        collect_from_editor_segment,
                        extract_and_ingest,
                    )
                    snippet = collect_from_editor_segment(
                        segment_text=seg["source"],
                        segments_meta_entry=seg_meta,
                        project_id=state.project_id,
                    )
                    if snippet is not None:
                        report = await loop.run_in_executor(
                            None,
                            lambda: extract_and_ingest(
                                [snippet], kg,
                            ),
                        )
                        if report.written > 0:
                            ui.notify(f"Citation extracted: {report.written} record(s)", type="positive")
                        request_kg_save(kg.save, delay=1.0)
                except Exception as e:
                    print(f"[promote_pair citation] {e}")

        except Exception as e:
            print(f"[promote_pair] {e}")

    async def _batch():
        if state.is_batch:
            return
            ui.notify("AI Translation is disabled", type="warning")
            return
        state.is_batch = True
        _render_batch_button()
        src, tgt = parse_lang_pair(state.lang_pair)
        loop = asyncio.get_running_loop()
        try:
            for seg in state.segments:
                if not state.is_batch:
                    break
                if seg["status"] == "done" or seg["target"].strip():
                    continue
                try:
                    a = tm.lookup_fuzzy(seg["source"], threshold=90.0, limit=1) if tm else []
                    b = glossary.lookup_terms(seg["source"], src, tgt) if glossary else []
                    c = tm.search_concordance(seg["source"], top_n=2) if tm else []
                    _, text = await loop.run_in_executor(
                        llm_executor,
                        lambda s=seg, a=a, b=b, c=c: translator.translate(
                            s["source"], src, tgt, a, b, c
                        ),
                    )
                    seg["target"] = text
                    if seg["id"] == state.active_index:
                        state.current["target"] = text
                except Exception as ex:
                    print(f"[batch] {ex}")
            state.request_autosave()
            state.notify("segments")
            if state.is_batch:
                ui.notify("Batch complete!", type="positive")
        finally:
            state.is_batch = False
            _render_batch_button()

    def _stop_batch():
        state.is_batch = False
        ui.notify("Batch stopped", type="warning")
        _render_batch_button()

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
        md = _compile_md(use_target=True)
        path = PROJECTS_DIR / f"compiled_target_{state.project_id}.docx"
        try:
            doc_parser.compile_to_designed_docx(md, path)
            if path.exists():
                ui.download(path.read_bytes(), f"translated_{state.filename}")
                path.unlink(missing_ok=True)
                return
        except Exception as e:
            print(f"[export target] {e}")
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
            print(f"[export source] {e}")
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
