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

from . import intel_panel, kg_search, predictions, segment_editor, segment_navigator, settings as ui_settings, target_search
from .state import WorkspaceState, request_kg_save
from translate_core.entity_extraction.ingest_helpers import ensure_agent
from translate_core import comments as cm
# Segment types excluded from concept promotion (noun-chunk noise). They still
# save to TM and run citation extraction. Defined at module level so tests and
# importers can reference the same vocabulary without loading the page function.
_CONCEPT_SKIP_TYPES = {
    "bibliography", "index", "bibliography_entry", "footnote",
    "book_metadata", "noise", "artist_header", "event_metadata",
    "institution_line", "festival_credit", "artwork_record",
}


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
    ui.query(".nicegui-content").classes("p-0 gap-0")

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
        "w-full flex-row items-center justify-between rounded-none px-5 py-3 shrink-0"
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
                on_click=lambda: _open_glossary(state, glossary, config, parse_lang_pair, kg, qa_engine, _slugify_project(state)),
            ).props("flat round dense size=sm color=grey-6").tooltip("Add Glossary Term")
            ui.button(
                icon="rate_review",
                on_click=lambda: ui.navigate.to(f"/review/{state.project_id}"),
            ).props("flat round dense size=sm color=grey-6").tooltip("Review")

        with ui.column().classes("w-44 items-center gap-0.5"):
            progress_pct_label = ui.label(f"{int(state.progress() * 100)}%").classes(
                "text-[10px] font-bold uppercase tracking-widest opacity-70"
            )
            progress_bar = ui.linear_progress(
                value=state.progress(), color="positive"
            ).props('size="6px" :show-value="false"').classes("w-full rounded-full")
            save_label = ui.label("✓ Saved").classes(
                "text-[9px] font-bold uppercase tracking-wider text-positive"
            )
        with ui.row().classes("gap-2 items-center"):
            ui.button(icon="menu_open", on_click=lambda: drawer.toggle()).props("flat round dense color=grey-6").tooltip("Toggle side panel")
            ui_settings.dark_toggle_button(dm)
            with ui.dropdown_button("Export", icon="file_download", auto_close=True).props(
                "rounded unelevated dense color=positive"
            ):
                ui.item("Translated Book (.docx)", on_click=lambda: _open_export_dialog("target_docx"))
                ui.item("Reorganized Source (.docx)", on_click=lambda: _open_export_dialog("source_docx"))
                ui.item("Plain .txt", on_click=lambda: _open_export_dialog("txt"))
                ui.separator()
                _style_indicator = ui.label("").classes("text-xs opacity-60 px-2 py-1")
                ui.item("Change house style…", on_click=lambda: _open_change_style_dialog(state, _style_indicator))
                ui.item("Manage publisher styles…", on_click=lambda: _open_style_manager(state, _style_indicator))
            from translate_core.publisher_styles import get_style_label
            _style_indicator.set_text(f"Style: {get_style_label(state.house_style)}")

    def _update_progress():
        p = state.progress()
        progress_value["value"] = p
        try:
            progress_bar.set_value(p)
            progress_pct_label.set_text(f"{int(p * 100)}%")
        except Exception:
            pass

    def _update_save_status():
        status = state.save_status
        if status == "saved":
            text, color = "✓ Saved", "text-positive"
        elif status == "saving":
            text, color = "Saving…", "text-amber-500"
        else:
            text, color = "● Unsaved", "text-grey-500"
        try:
            with state.client:
                save_label.set_text(text)
                save_label.classes(
                    remove="text-positive text-amber-500 text-grey-500",
                    add=color,
                )
        except Exception:
            pass

    state.subscribe("segments", _update_progress)
    state.subscribe("save_status", _update_save_status)
    # Fire once so the indicator reflects the initial state (usually "saved").
    _update_save_status()
    # ------------------------------------------------------------------
    # Right drawer: segment navigator + KG search
    # ------------------------------------------------------------------
    with (
        ui.right_drawer(value=True, fixed=True)
        .props('width=380 bordered :breakpoint="1280"')) as drawer:
        with ui.column().classes("w-full p-4 gap-2"):
            segment_navigator.build(state)
            kg_search.build(state, deps)


    with ui.column().classes("w-full items-center gap-0"):
        with ui.column().classes("w-full max-w-4xl px-4 pt-2 pb-2 gap-2"):
            def _trigger_confirm() -> None:
                background_tasks.create(_confirm_segment(), name="confirm_btn")

            # TM + Glossary band — fixed height above the editor so the
            # target field anchors at ~2/3 viewport on portrait screens.
            tm_gl_zone = ui.column().classes("w-full")

            segment_editor.build(state, deps, on_confirm=_trigger_confirm)

            # KG zone — fills all remaining viewport below the editor.
            kg_zone = ui.column().classes("w-full")

            intel_panel.build(state, deps, tm_gl_slot=tm_gl_zone, kg_slot=kg_zone)

            # Find & Replace in target segments — below KG.
            target_search.build(state, deps)


    # ------------------------------------------------------------------
    # Confirm + batch + KG/TM promotion
    # ------------------------------------------------------------------
    # _CONCEPT_SKIP_TYPES is defined at module level so consumers (tests,
    # import helpers) can reference it without entering the page function.

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

        seg_meta = None
        meta_list = state._segments_meta
        if 0 <= seg_index < len(meta_list):
            seg_meta = meta_list[seg_index]

        seg_type = seg_meta.get("type") if seg_meta else None

        # Save to TM for every confirmed segment.
        try:
            await loop.run_in_executor(
                None,
                lambda: save_pair_to_tm(seg["source"], seg["target"], lang_pair),
            )
        except Exception as e:
            log.warning(f"promote_pair TM save: {e}")

        # Concept promotion is gated to prose segments only. Apparatus types
        # (footnote, bibliography, metadata) produce noisy noun chunks; they are
        # still handled by the citation extraction path below.
        _proj_slug = _slugify_project(state)
        should_promote = kg is not None and seg_type not in _CONCEPT_SKIP_TYPES
        # reload_if_changed is only useful on the first confirm after page load
        # (to pick up maintenance-script edits). After that, the only writer to
        # knowledge.db is this process via the debounced kg.save, whose mtime
        # we track — so subsequent stat checks are wasted work.
        if not getattr(state, "_kg_reloaded", False):
            await loop.run_in_executor(None, kg.reload_if_changed)
            state._kg_reloaded = True
        _needs_kg_save = False
        try:
            if should_promote:
                # Ensure the project container exists with correct project_type
                # and translated_by edge (O-20).
                await loop.run_in_executor(
                    None,
                    lambda: _ensure_project_container(
                        kg, _proj_slug, state.filename or _proj_slug, state.project_type
                    ),
                )
                _needs_kg_save = True

            # Live smol entity extraction runs for every segment. Apparatus
            # segments now have a correct ``type`` in ``segments_meta`` so
            # collect_from_editor_segment formats them as citation snippets.
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
                        _needs_kg_save = True
                except Exception as e:
                    log.warning(f"promote_pair entity extraction: {e}")

            # Single coalesced save with a generous debounce so the 50MB
            # serialize-and-write doesn't fire while the user is mid-keystroke
            # on the next segment. Multiple confirms within the window collapse
            # into one disk write.
            if _needs_kg_save:
                request_kg_save(kg.save, delay=5.0)
        except Exception as e:
            log.error(f"promote_pair: {e}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _compile_md(use_target: bool, comments_mode: str = cm.EXPORT_NONE) -> str:
        """Build the markdown stream for DOCX export.

        Footnote definitions that span multiple segments are joined with a
        space into one line so compile_to_designed_docx's line-based
        [^N]: parser sees the full footnote text. Without this, continuation
        segments (no [^N]: prefix) fall through to body_lines and render as
        orphan body paragraphs.

        When *comments_mode* is not ``EXPORT_NONE``, segment comments are
        injected into the markdown stream:
        - footnote-definition segments: comments are appended to the
          footnote text (rendered at the bottom of the page next to their
          footnote number);
        - body segments: comments are appended as a blockquote paragraph
          immediately after the segment text.
        """
        import re as _re
        fn_def_re = _re.compile(r"^\[\^(\w+)\]:")
        lines: list[str] = []
        i = 0
        segs = state.segments
        while i < len(segs):
            s = segs[i]
            src = s.get("source", "").lstrip()
            if fn_def_re.match(src):
                # Collect this footnote's def + continuation segments.
                j = i + 1
                while j < len(segs):
                    nxt = segs[j].get("source", "").lstrip()
                    if fn_def_re.match(nxt):
                        break
                    j += 1
                parts = []
                for k in range(i, j):
                    seg = segs[k]
                    txt = seg.get("target", "").strip() if use_target else ""
                    if not txt:
                        txt = seg.get("source", "").strip()
                    if txt:
                        parts.append(txt)
                # Inject comments into footnote definition text.
                fn_text = " ".join(parts)
                if comments_mode != cm.EXPORT_NONE:
                    for k in range(i, j):
                        seg = segs[k]
                        c_text = cm.format_comments_for_export(seg, comments_mode)
                        if c_text:
                            fn_text += c_text
                lines.append(fn_text)
                i = j
            else:
                txt = s.get("target", "").strip() if use_target else ""
                if not txt:
                    txt = s.get("source", "").strip()
                if txt:
                    lines.append(txt)
                # Inject body-segment comments as a blockquote paragraph.
                if comments_mode != cm.EXPORT_NONE:
                    c_text = cm.format_comments_for_export(s, comments_mode)
                    if c_text:
                        lines.append(c_text)
                i += 1
        return "\n\n".join(lines)

    from translate_core.publisher_styles import resolve_typography
    _house_typo = resolve_typography(state.house_style)


    def _open_export_dialog(export_type: str) -> None:
        """Dialog with comments-in-export options before running the export."""
        with ui.dialog() as dialog, ui.card().classes("min-w-[420px]"):
            ui.label("Export options").classes("text-h6")
            ui.label(
                "Comments can be embedded in the exported document:"
            ).classes("text-xs opacity-70 mt-1")
            mode = ui.toggle(
                {
                    cm.EXPORT_NONE: "No comments (clean)",
                    cm.EXPORT_TRANSLATOR_ONLY: "Translator only",
                    cm.EXPORT_ALL: "All comments",
                },
                value=cm.EXPORT_NONE,
            ).classes("w-full mt-2")
            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=lambda: dialog.close()).props("flat")
                ui.button(
                    "Export",
                    icon="file_download",
                    on_click=lambda: (_run_export(export_type, mode.value), dialog.close()),
                ).props("unelevated color=positive")
        dialog.open()

    def _run_export(export_type: str, comments_mode: str) -> None:
        if export_type == "target_docx":
            _export_target_docx(comments_mode)
        elif export_type == "source_docx":
            _export_source_docx(comments_mode)
        elif export_type == "txt":
            _export_txt(comments_mode)

    def _export_target_docx(comments_mode: str = cm.EXPORT_NONE):
        if state.pipeline == "simple":
            # Simple pipeline: preserve original formatting via template
            template = PROJECTS_DIR / f"{state.project_id}.docx"
            if template.exists():
                # Re-link paragraph indices if autosave stripped them
                if not any("docx_para_idx" in s for s in state.segments):
                    n_linked = doc_parser.relink_docx_para_idx(
                        template, state.segments
                    )
                    n_translated = sum(
                        1 for s in state.segments if s.get("target", "").strip()
                    )
                    msg = f"Re-linked {n_linked}/{n_translated} segments to original layout"
                    if n_linked < n_translated:
                        ui.notify(msg, type="warning")
                    else:
                        ui.notify(msg)
                    state.request_autosave()
                out = PROJECTS_DIR / f"compiled_target_{state.project_id}.docx"
                try:
                    doc_parser.compile_from_template(
                        template, out, state.segments,
                        comments_mode=comments_mode,
                    )
                    if out.exists():
                        ui.download(out.read_bytes(), f"translated_{state.filename}")
                        out.unlink(missing_ok=True)
                        return
                except Exception as e:
                    log.error(f"template export: {e}")
            else:
                ui.notify(
                    "No original DOCX — exporting with generic styling",
                    type="warning",
                )
            # Fallback: academic-style DOCX compilation
            md = _compile_md(use_target=True, comments_mode=comments_mode)
            path = PROJECTS_DIR / f"compiled_target_{state.project_id}.docx"
            try:
                doc_parser.compile_to_designed_docx(md, path, house_typography=_house_typo)
                if path.exists():
                    ui.download(path.read_bytes(), f"translated_{state.filename}")
                    path.unlink(missing_ok=True)
                    return
            except Exception as e:
                log.error(f"export target: {e}")
            ui.notify("Export failed", type="negative")
        elif state.pipeline == "academic":
            # Academic pipeline: always restyled DOCX with real footnotes
            from translate_core.doc_parser import footnote_alignment_report
            report = footnote_alignment_report(state.segments)
            if not report["aligned"]:
                ui.notify(
                    f"{report['refs']} footnote refs vs {report['defs']} definitions — "
                    "misnumbered footnotes will be wrong in the DOCX; "
                    "run scripts/repair_book_footnotes.py",
                    type="warning",
                )
            md = _compile_md(use_target=True, comments_mode=comments_mode)
            path = PROJECTS_DIR / f"compiled_target_{state.project_id}.docx"
            try:
                doc_parser.compile_to_designed_docx(md, path, segments=state.segments, house_typography=_house_typo)
                if path.exists():
                    ui.download(path.read_bytes(), f"translated_{state.filename}")
                    path.unlink(missing_ok=True)
                    return
            except Exception as e:
                log.error(f"export target: {e}")
            ui.notify("Export failed", type="negative")
        else:
            # Unknown pipeline — same as simple fallback
            md = _compile_md(use_target=True, comments_mode=comments_mode)
            path = PROJECTS_DIR / f"compiled_target_{state.project_id}.docx"
            try:
                doc_parser.compile_to_designed_docx(md, path, house_typography=_house_typo)
                if path.exists():
                    ui.download(path.read_bytes(), f"translated_{state.filename}")
                    path.unlink(missing_ok=True)
                    return
            except Exception as e:
                log.error(f"export target: {e}")
            ui.notify("Export failed", type="negative")

    def _export_source_docx(comments_mode: str = cm.EXPORT_NONE):
        md = _compile_md(use_target=False, comments_mode=comments_mode)
        path = PROJECTS_DIR / f"compiled_source_{state.project_id}.docx"
        try:
            doc_parser.compile_to_designed_docx(md, path, house_typography=_house_typo)
            if path.exists():
                ui.download(path.read_bytes(), f"reorganized_source_{state.filename}")
                path.unlink(missing_ok=True)
                return
        except Exception as e:
            log.error(f"export source: {e}")
        ui.notify("Export failed", type="negative")

    def _export_txt(comments_mode: str = cm.EXPORT_NONE):
        content = _compile_md(use_target=True, comments_mode=comments_mode)
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


def _slugify_project(state) -> str:
    """Derive a project slug from filename or project_id — used by both
    glossary dialog and promote_pair to form source:<slug> container ids."""
    import re as _re
    base = (state.filename or state.project_id or "project").lower()
    return (_re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:80]) or "project"

def _ensure_project_container(kg, slug: str, title: str, project_type: str) -> None:
    """Create the source-text container node and translated_by edge if absent."""
    if not kg.G.has_node(f"source:{slug}"):
        kg.add_source_text_node(slug, title=title, project_type=project_type)
    ensure_agent(kg, "Urban Belina", role="translator")
    kg.link_translated_by(slug, "urban-belina")


# ---------------------------------------------------------------------------
# Glossary dialog (module-level for cleanliness; called from top bar)
# ---------------------------------------------------------------------------
def _open_glossary(state, glossary, config, parse_lang_pair, kg=None, qa_engine=None, proj_slug=""):
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

        def _do_save() -> bool:
            """Core save logic; returns True on success so callers decide UI."""
            s = (src_input.value or "").strip()
            t = (tgt_input.value or "").strip()
            if not (s and t):
                ui.notify("Both terms are required", type="negative")
                return False
            config.GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
            custom_path = config.GLOSSARY_DIR / "custom.tsv"
            line = f"{s}\t{t}\t{note_input.value or ''}\n"
            with open(custom_path, "a", encoding="utf-8") as f:
                f.write(line)
            if glossary is not None:
                glossary.add_entry(s, t, src_lang, tgt_lang, note=note_input.value or "")
            # Push the term to the KG, tagged with the current project so the
            # mapping carries "made while translating this project" provenance.
            # Done off the UI thread; debounced save avoids clobbering external edits.
            if kg is not None:
                note_val = note_input.value or ""
                lineage_val = (lineage_input.value or "").strip() or "general"
                slug = proj_slug or _slugify_project(state)

                async def _kg_add():
                    loop = asyncio.get_running_loop()
                    try:
                        def _do():
                            kg.reload_if_changed()
                            _ensure_project_container(kg, slug, state.filename or "current project", state.project_type)
                            sid = kg.add_term_node(s, src_lang, is_phrase=" " in s)
                            tid = kg.add_term_node(t, tgt_lang, is_phrase=" " in t)
                            kg.link_translations_with_context(
                                src_term_id=sid, tgt_term_id=tid, confidence=1.0,
                                lineage=lineage_val, gloss=note_val, verified=True,
                                source_text_id=slug,
                            )
                        await loop.run_in_executor(None, _do)
                        request_kg_save(kg.save, delay=5.0)
                        if qa_engine is not None and glossary is not None:
                            await loop.run_in_executor(None, lambda: qa_engine.build_lemma_index(glossary.entries))
                        with state.client:
                            ui.notify(f"'{s} → {t}' synced to KG", type="positive")
                    except Exception as e:
                        log.warning(f"glossary KG sync: {e}")
                        with state.client:
                            ui.notify("KG sync failed — term kept in glossary file; run scripts/ingest_glossary_to_kg.py --apply to re-sync", type="warning")

                background_tasks.create(_kg_add(), name="glossary_kg")
            return True

        def _save():
            if _do_save():
                ui.notify("Term added to glossary", type="positive")
                dialog.close()

        def _save_and_add():
            if _do_save():
                src_input.set_value("")
                tgt_input.set_value("")
                note_input.set_value("")

        with ui.row().classes("w-full justify-end mt-4 gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save & add another", on_click=_save_and_add).props("outline")
            ui.button("Save", on_click=_save).props("color=positive")
    dialog.open()


# ---------------------------------------------------------------------------
# Publisher house-style dialogs (module-level, called from Export dropdown)
# ---------------------------------------------------------------------------
def _open_change_style_dialog(state, style_indicator):
    """Dialog to switch the project's house_style to a different profile."""
    from translate_core.publisher_styles import get_style_options, get_style_label

    with ui.dialog() as dialog, ui.card().classes("min-w-[360px]"):
        ui.label("Change house style").classes("text-lg font-bold mb-2")
        options = get_style_options()
        style_select = ui.select(
            options,
            label="Publisher",
            value=state.house_style,
        ).classes("w-full")

        async def _apply():
            chosen = style_select.value
            if chosen and chosen != state.house_style:
                state.house_style = chosen
                state.request_autosave()
                style_indicator.set_text(f"Style: {get_style_label(chosen)}")
                ui.notify(f"House style set to {get_style_label(chosen)}", type="positive")
            dialog.close()

        with ui.row().classes("w-full justify-end mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Apply", on_click=_apply).props("color=positive")
    dialog.open()


def _open_style_manager(state, style_indicator):
    """Dialog to create, edit, and delete publisher style profiles.

    CRUD over data/publisher_styles.json. The 'maska' seed profile is
    protected — it can be edited but not deleted.
    """
    from translate_core.publisher_styles import load_styles, save_styles

    with ui.dialog() as dialog, ui.card().classes("min-w-[520px]"):
        ui.label("Manage publisher styles").classes("text-lg font-bold mb-2")
        ui.label(
            "Create, edit, or delete typography profiles for different publishers. "
            "The Maska seed profile can be edited but not deleted."
        ).classes("text-caption opacity-70 mb-3")

        list_column = ui.column().classes("w-full gap-1")

        def _refresh_list():
            list_column.clear()
            styles_local = load_styles()
            with list_column:
                for key, prof in styles_local.items():
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("flex-1"):
                            ui.label(prof.get("label", key)).classes("text-sm font-bold")
                            ui.label(
                                f"{prof.get('body_font', '?')} {prof.get('body_size_pt', '?')}pt · "
                                f"{prof.get('page_size', '?')} · {prof.get('margins_in', '?')}″"
                            ).classes("text-[10px] opacity-60")
                        with ui.row().classes("gap-1"):
                            ui.button(
                                "Edit",
                                on_click=lambda k=key: _open_edit_form(k),
                            ).props("flat dense size=sm")
                            if key != "maska":
                                ui.button(
                                    "Delete",
                                    on_click=lambda k=key: _delete_style(k),
                                    color="negative",
                                ).props("flat dense size=sm")
                            else:
                                ui.button("Delete").props("flat dense size=sm disable")
            ui.button(
                "+ Add new style",
                on_click=lambda: _open_edit_form(None),
            ).props("flat dense color=primary").classes("mt-2")

        def _delete_style(key: str):
            styles_local = load_styles()
            if key in styles_local and key != "maska":
                del styles_local[key]
                save_styles(styles_local)
                ui.notify(f"Style '{key}' deleted", type="info")
                _refresh_list()

        edit_form_column = ui.column().classes("w-full")

        def _open_edit_form(key):
            """Open an inline edit form for a profile (key=None for new)."""
            edit_form_column.clear()
            styles_local = load_styles()
            prof = styles_local.get(key, {}) if key else {}
            with edit_form_column:
                ui.separator().classes("my-2")
                ui.label("Edit style" if key else "New style").classes("text-sm font-bold mb-1")
                key_input = ui.input(
                    "Style key (slug, e.g. studia_humanitatis)",
                    value=key or "",
                ).props("outlined dense").classes("w-full")
                key_input.set_enabled(key is None)  # can't rename existing
                label_input = ui.input("Label", value=prof.get("label", "")).props("outlined dense").classes("w-full")
                font_input = ui.input("Body font", value=prof.get("body_font", "Times New Roman")).props("outlined dense").classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    body_size = ui.number("Body size (pt)", value=prof.get("body_size_pt", 12)).props("outlined dense").classes("w-full")
                    line_spacing = ui.number("Line spacing", value=prof.get("line_spacing", 1.5)).props("outlined dense").classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    page_size = ui.select(["A4", "Letter"], value=prof.get("page_size", "A4")).props("outlined dense").classes("w-full")
                    margins = ui.number("Margins (inch)", value=prof.get("margins_in", 1.0)).props("outlined dense").classes("w-full")
                space_after = ui.number("Space after (pt)", value=prof.get("space_after_pt", 0)).props("outlined dense").classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    fn_size = ui.number("Footnote size (pt)", value=prof.get("footnote_size_pt", 10)).props("outlined dense").classes("w-full")
                    fn_spacing = ui.number("Footnote line spacing", value=prof.get("footnote_line_spacing", 1.0)).props("outlined dense").classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    bq_size = ui.number("Blockquote size (pt)", value=prof.get("blockquote_size_pt", 11)).props("outlined dense").classes("w-full")
                    bq_indent = ui.number("Blockquote indent (inch)", value=prof.get("blockquote_indent_in", 0.5)).props("outlined dense").classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    h1_size = ui.number("H1 size (pt)", value=prof.get("h1_size_pt", 18)).props("outlined dense").classes("w-full")
                    h2_size = ui.number("H2 size (pt)", value=prof.get("h2_size_pt", 13)).props("outlined dense").classes("w-full")

                def _save_form():
                    slug = (key_input.value or "").strip()
                    if not slug:
                        ui.notify("Style key is required", type="negative")
                        return
                    if not (label_input.value or "").strip():
                        ui.notify("Label is required", type="negative")
                        return
                    new_prof = {
                        "label": label_input.value.strip(),
                        "body_font": font_input.value or "Times New Roman",
                        "body_size_pt": float(body_size.value or 12),
                        "line_spacing": float(line_spacing.value or 1.5),
                        "page_size": page_size.value or "A4",
                        "margins_in": float(margins.value or 1.0),
                        "space_after_pt": float(space_after.value or 0),
                        "para_first_line_indent_in": 0.0,
                        "footnote_size_pt": float(fn_size.value or 10),
                        "footnote_line_spacing": float(fn_spacing.value or 1.0),
                        "blockquote_size_pt": float(bq_size.value or 11),
                        "blockquote_line_spacing": 1.0,
                        "blockquote_indent_in": float(bq_indent.value or 0.5),
                        "h1_size_pt": float(h1_size.value or 18),
                        "h2_size_pt": float(h2_size.value or 13),
                    }
                    all_styles = load_styles()
                    all_styles[slug] = new_prof
                    save_styles(all_styles)
                    ui.notify(f"Style '{slug}' saved", type="positive")
                    edit_form_column.clear()
                    _refresh_list()

                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button("Cancel", on_click=lambda: edit_form_column.clear()).props("flat")
                    ui.button("Save", on_click=_save_form).props("color=positive")

        _refresh_list()

        with ui.row().classes("w-full justify-end mt-4"):
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()
