# main.py
#
# Bel Translation Suite — Main Application Entrypoint
# Fully integrated with advanced document pre-processing and compiled DOCX exports
#

import os
import re
import sys
import logging
import uuid
from datetime import datetime
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, Tuple


log = logging.getLogger(__name__)
try:
    from nicegui import app, context, ui

    import app_state
    import config
    from translate_core import (
        DocumentParser,
        Glossary,
        KnowledgeGraph,
        QAEngine,
        TranslationMemory,
    )
except ImportError as e:
    log.error(f"Import failed: {e.name}")
    sys.exit(1)

# Global resources
# ---------------------------------------------------------------------------
tm: "TranslationMemory | None" = None
glossary: "Glossary | None" = None
kg: "KnowledgeGraph | None" = None
doc_parser: "DocumentParser | None" = None
qa_engine: "QAEngine | None" = None
GLOBAL_VOCAB: Dict[str, set] = {}  # Project ID -> Set of words

# ---------------------------------------------------------------------------
# Project persistence
# ---------------------------------------------------------------------------
PROJECTS_DIR = config.BASE_DIR / "data" / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Publish module-level paths to the shared app_state immediately.
# Functions are wired further down once defined.
app_state.PROJECTS_DIR = PROJECTS_DIR
app_state.config = config


def validate_project_id(project_id: str) -> None:
    """Raise ValueError if project_id contains path traversal or unexpected chars."""
    if not re.fullmatch(r"[a-f0-9\-]{6,36}", project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")


def _is_review_expired(r: dict) -> bool:
    """Check if a review clone's funnel has expired."""
    exp = r.get("funnel_expires_at", "")
    if not exp:
        return True
    try:
        return datetime.fromisoformat(exp) <= datetime.now()
    except Exception:
        return True


def parse_lang_pair(pair: str) -> Tuple[str, str]:
    """Return (src, tgt) from 'src->tgt'. Defaults to ('en', 'sl')."""
    if not pair or "->" not in pair:
        return "en", "sl"
    src, _, tgt = pair.partition("->")
    return (src or "en"), (tgt or "sl")


def _count_target_text(segments: list) -> tuple[int, int, float]:
    """Return (chars_with_spaces, chars_without_spaces, pages) from target text.

    Counts ONLY seg["target"] — never source, comments, or metadata.
    pages = chars_without_spaces / 1500 (exact decimal).
    """
    chars_with = sum(len(s.get("target", "")) for s in segments)
    chars_without = sum(len(re.sub(r"\s+", "", s.get("target", ""))) for s in segments)
    pages = chars_without / 1500 if chars_without else 0.0
    return chars_with, chars_without, pages


def list_projects() -> list:
    projects = []
    for p in PROJECTS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            segs = data.get("segments", [])
            chars_with, chars_without, pages = _count_target_text(segs)
            projects.append(
                {
                    "id": data["id"],
                    "filename": data["filename"],
                    "lang_pair": data.get("lang_pair", "en->sl"),
                    "saved_at": data["saved_at"],
                    "total": data["total"],
                    "done": data["done"],
                    "chars_with": chars_with,
                    "chars_without": chars_without,
                    "pages": pages,
                    "billing_rate": data.get("billing_rate"),
                }
            )
        except Exception:
            pass
    projects.sort(key=lambda x: x["saved_at"], reverse=True)
    return projects


def save_project(ws: dict):
    validate_project_id(ws["project_id"])
    segs = [dict(s) for s in ws["segments"]]
    done = sum(1 for s in segs if s.get("status") == "done")
    data = {
        "id": ws["project_id"],
        "filename": ws["filename"],
        "lang_pair": ws["lang_pair"],
        "active_index": ws["active_index"],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(segs),
        "done": done,
        "segments": segs,
    }
    # Persist these unconditionally, not only when truthy. The prior
    # `if ws.get(k)` guard silently dropped segments_meta when it was an
    # empty list, losing role classification on round-trip. Per-segment
    # manifest keys (pdf_para_idx, heading_level, …) ride inside `segments`.
    for k in ("pipeline", "project_type", "segments_meta", "house_style", "billing_rate"):
        if k in ws:
            data[k] = ws[k]
    path = PROJECTS_DIR / f"{ws['project_id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_project(project_id: str):
    validate_project_id(project_id)
    path = PROJECTS_DIR / f"{project_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data is not None:
        from translate_core import comments as cm
        segs = data.get("segments", [])
        # Only run migration if any segment lacks the "comments" key.
        # After the first load, all segments carry "comments" and this is a no-op.
        if segs and any("comments" not in s for s in segs):
            for seg in segs:
                cm.migrate_legacy_segment(seg)
    return data


def delete_project(project_id: str):
    validate_project_id(project_id)
    for suffix in [".json", ".docx", ".pdf", ".txt"]:
        p = PROJECTS_DIR / f"{project_id}{suffix}"
        if p.exists():
            p.unlink()


def _save_billing_rate(project_id: str, rate: float | None) -> None:
    """Patch billing_rate into a project JSON without rewriting segments."""
    validate_project_id(project_id)
    path = PROJECTS_DIR / f"{project_id}.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["billing_rate"] = rate
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Working TM
# ---------------------------------------------------------------------------
def save_pair_to_tm(source: str, target: str, lang_pair: str):
    """Upsert a translator-confirmed segment into the working TMX.

    Semantics (no duplicates ever):
      - if the same source already exists with the same target → no write
      - if the same source exists with a different target → rewrite the
        existing <tu>'s target seg in place
      - otherwise → append a new <tu>

    Both the on-disk TMX and the in-memory `tm.entries` list stay in
    sync so fuzzy lookup sees the updated target immediately.
    """
    src_lang, tgt_lang = parse_lang_pair(lang_pair)
    tm_path = config.TM_DIR / "working.tmx"
    config.TM_DIR.mkdir(parents=True, exist_ok=True)

    if not tm_path.exists():
        header = (
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8"?>',
                    '<tmx version="1.4">',
                    f'  <header creationtool="ZenTranslator" srclang="{src_lang}"/>',
                    "  <body>",
                    "  </body>",
                    "</tmx>",
                ]
            )
            + "\n"
        )
        tm_path.write_text(header, encoding="utf-8")

    def escape_xml_entities(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    src_n = (source or "").strip()
    tgt_n = (target or "").strip()
    if not src_n or not tgt_n:
        return

    raw = tm_path.read_text(encoding="utf-8")

    # Look for an existing <tu> whose source-seg matches src_n exactly.
    # Each TU follows the shape produced below (or by parse_tmx_file), so
    # we can match the source seg via a non-greedy regex pegged to the
    # source-language tuv.
    src_escaped = escape_xml_entities(src_n)
    tgt_escaped = escape_xml_entities(tgt_n)
    tu_pattern = re.compile(
        r"(    <tu>\s*"
        r'      <tuv xml:lang="' + re.escape(src_lang) + r'"><seg>'
        + re.escape(src_escaped) + r"</seg></tuv>\s*"
        r'      <tuv xml:lang="' + re.escape(tgt_lang) + r'"><seg>)'
        r"(.*?)"
        r"(</seg></tuv>\s*    </tu>\n?)",
        re.DOTALL,
    )

    new_raw: str | None = None
    match_existing = tu_pattern.search(raw)
    if match_existing is not None:
        existing_target = match_existing.group(2)
        if existing_target == tgt_escaped:
            # Identical pair — nothing to do on disk.
            pass
        else:
            # Rewrite the target seg in place.
            new_raw = (
                raw[: match_existing.start()]
                + match_existing.group(1)
                + tgt_escaped
                + match_existing.group(3)
                + raw[match_existing.end():]
            )
    else:
        # New pair — append before </body>.
        tu = (
            "\n".join(
                [
                    "    <tu>",
                    f'      <tuv xml:lang="{src_lang}"><seg>{src_escaped}</seg></tuv>',
                    f'      <tuv xml:lang="{tgt_lang}"><seg>{tgt_escaped}</seg></tuv>',
                    "    </tu>",
                ]
            )
            + "\n"
        )
        new_raw = raw.replace("  </body>", tu + "  </body>")

    if new_raw is not None and new_raw != raw:
        tm_path.write_text(new_raw, encoding="utf-8")

    # Mirror the upsert in the in-memory TM so lookup_fuzzy sees the
    # change without a reload.
    if tm is not None:
        tm.upsert_runtime_pair(src_n, tgt_n, src_lang, tgt_lang)


async def init_resources():
    """Construct backend singletons and expose them via app_state so the UI
    layer reads live values regardless of which Python process owns this
    module (matters with NiceGUI's auto-reload: the worker runs as
    __mp_main__, not __main__)."""
    global tm, glossary, kg, doc_parser, qa_engine
    try:
        tm = TranslationMemory()
        app_state.tm = tm
        glossary = Glossary()
        app_state.glossary = glossary
        kg = KnowledgeGraph()
        app_state.kg = kg
        doc_parser = DocumentParser()
        app_state.doc_parser = doc_parser
        qa_engine = QAEngine()
        qa_engine.build_lemma_index(glossary.entries)
        app_state.qa_engine = qa_engine
    except Exception as e:
        # Make startup failures loud so the UI doesn't silently see None
        # resources later.
        import traceback
        log.error(f"init_resources failed: {e}")
        traceback.print_exc()
        return

# Register the startup handler idempotently. NiceGUI's testing plugin re-runs
# main.py via runpy for every test, and `@app.on_startup` raises RuntimeError
# the second time because the framework is already started. Catching it lets
# the first registration win and subsequent calls be no-ops.
try:
    app.on_startup(init_resources)
except RuntimeError:
    pass


# ===========================================================================
# PAGE /  —  Project list
# ===========================================================================
@ui.page("/")
def page_home():
    from ui import settings as ui_settings  # local import to avoid circulars at module load

    apply_colors()
    dm = ui_settings.install_dark_mode()

    # Capture the client immediately during page construction
    home_client = context.client

    ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")

    with ui.column().classes("w-full min-h-screen"):
        with ui.row().classes(
            "w-full px-8 py-12 items-center justify-between shadow-lg text-white"
        ).style(
            "background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);"
        ):
            with ui.row().classes("items-center gap-4"):
                ui.icon("blur_on", size="56px").props("color=primary").classes("animate-pulse")
                with ui.column().classes("gap-0"):
                    ui.label("Bel Translation Suite").classes(
                        "text-3xl font-black tracking-tighter"
                    )
                    ui.label("Professional Translation Workspace").classes(
                        "text-xs font-bold uppercase tracking-[0.2em] opacity-60"
                    )

            with ui.row().classes("gap-3 items-center"):
                ui.button(icon="account_tree", on_click=lambda: ui.navigate.to("/kg")).props("flat round dense color=white").tooltip("Knowledge Graph editor")
                ui.button(icon="compare_arrows", on_click=lambda: ui.navigate.to("/aligner")).props("flat round dense color=white").tooltip("Document Aligner")
                ui.button(icon="dark_mode", on_click=lambda: dm.toggle()).props(
                    "flat round dense color=white"
                ).tooltip("Toggle dark mode")
                ui.label("V 2.0").classes(
                    "text-[10px] font-black border border-white/10 px-2 py-1 rounded opacity-50"
                )

        with ui.column().classes("w-full max-w-4xl mx-auto px-6 -mt-8 gap-8 pb-20"):
            with ui.card().classes("w-full p-8 rounded-3xl shadow-2xl"):
                with ui.row().classes("w-full items-center justify-between mb-6"):
                    with ui.column().classes("gap-1"):
                        ui.label("Start a New Translation").classes("text-xl font-bold")
                        ui.label(
                            "Upload a .docx or .pdf book file to begin your project"
                        ).classes("text-sm opacity-60")

                    with ui.card().classes("flex-row gap-3 items-center p-2 rounded-2xl").props("flat bordered"):
                        lang_opts = ["en", "sl", "de", "fr", "it"]
                        src_lang = (
                            ui.select(lang_opts, value="en")
                            .props("outlined dense rounded")
                            .classes("w-20")
                        )
                        ui.icon("swap_horiz", size="sm").props("color=grey-5")
                        tgt_lang = (
                            ui.select(lang_opts, value="sl")
                            .props("outlined dense rounded")
                            .classes("w-20")
                        )


                async def upload_wrapper(e):
                    await handle_new_upload(
                        e,
                        f"{src_lang.value}->{tgt_lang.value}",
                    )

                ui.upload(
                    on_upload=upload_wrapper,
                    auto_upload=True,
                    label="Drop files here or click to browse",
                    max_files=1,
                    max_file_size=config.MAX_UPLOAD_SIZE_MB * 1024 * 1024,
                ).classes("w-full").props(
                    "color=accent accept=.docx,.pdf flat bordered"
                )

            proj_container = ui.column().classes("w-full gap-4")
            render_project_list(proj_container, home_client)


def render_project_list(container: ui.column, client):
    container.clear()
    projects = list_projects()
    with container:
        if not projects:
            with ui.column().classes("w-full items-center py-20 opacity-20"):
                ui.icon("folder_open", size="64px")
                ui.label("No active projects").classes("text-lg font-bold")
            return

        # ── Shared billing state: { project_id: {"rate": float, "project": dict} } ──
        billing_state: dict[str, dict] = {}
        # Track checkbox UI elements for the export bar
        selected: dict[str, bool] = {}

        ui.label("RECENT PROJECTS").classes(
            "text-[10px] font-black uppercase tracking-[0.3em] px-2 mb-2 opacity-60"
        )

        # ── Export bar ──
        with ui.row().classes("w-full items-center justify-between px-2 mb-1"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("receipt_long", size="18px").props("color=primary")
                _selection_label = ui.label("Select projects below to export a billing report").classes(
                    "text-[10px] font-medium opacity-60"
                )

            with ui.row().classes("items-center gap-1"):
                _pdf_btn = ui.button("PDF", icon="picture_as_pdf").props(
                    "flat dense no-caps color=primary size=sm"
                ).classes("text-[10px]").tooltip("Export selected projects as PDF invoice")
                _xlsx_btn = ui.button("XLSX", icon="table_chart").props(
                    "flat dense no-caps color=primary size=sm"
                ).classes("text-[10px]").tooltip("Export selected projects as XLSX spreadsheet")

        def _get_selected_projects() -> list:
            """Collect billing dicts for all checked projects."""
            out = []
            for pid, st in billing_state.items():
                if st.get("_checked"):
                    p = st["project"]
                    out.append({
                        "filename": p["filename"],
                        "lang_pair": p["lang_pair"],
                        "chars_with": p["chars_with"],
                        "chars_without": p["chars_without"],
                        "pages": p["pages"],
                        "rate": st.get("rate", 0.0) or 0.0,
                    })
            return out

        def _update_selection_label():
            n = sum(1 for st in billing_state.values() if st.get("_checked"))
            if n == 0:
                _selection_label.set_text("Select projects below to export a billing report")
                _selection_label.classes(remove="text-primary")
            else:
                _selection_label.set_text(f"{n} project{'s' if n != 1 else ''} selected for export")
                _selection_label.classes(add="text-primary")

        def _export_pdf():
            sel = _get_selected_projects()
            if not sel:
                ui.notify("Select at least one project first.", type="warning")
                return
            from translate_core.billing_report import generate_pdf
            data = generate_pdf(sel)
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            ui.download(data, filename=f"billing_report_{ts}.pdf")
            ui.notify(f"PDF exported: {len(sel)} projects", type="positive")

        def _export_xlsx():
            sel = _get_selected_projects()
            if not sel:
                ui.notify("Select at least one project first.", type="warning")
                return
            from translate_core.billing_report import generate_xlsx
            data = generate_xlsx(sel)
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            ui.download(data, filename=f"billing_report_{ts}.xlsx")
            ui.notify(f"XLSX exported: {len(sel)} projects", type="positive")

        _pdf_btn.on("click", _export_pdf)
        _xlsx_btn.on("click", _export_xlsx)

        with ui.element("div").classes("w-full flex flex-col gap-4"):
            for p in projects:
                pct = int(p["done"] / p["total"] * 100) if p["total"] else 0
                with ui.row().classes("w-full gap-3 items-stretch"):
                    # ── Project card (clickable → open workspace) ──
                    with (
                        ui.card()
                        .props("flat bordered")
                        .classes("flex-1 p-4 rounded-2xl flex flex-row gap-4 cursor-pointer hover:shadow-lg transition break-inside-avoid")
                        .on(
                            "click", lambda pid=p["id"]: ui.navigate.to(f"/translate/{pid}")
                        )
                    ):
                        # ── LEFT: project info (title, progress, saved date) ──
                        with ui.column().classes("flex-1 min-w-0 gap-2"):
                            with ui.row().classes("w-full items-start justify-between"):
                                with ui.column().classes("gap-0.5 flex-1 min-w-0"):
                                    ui.label(p["filename"]).classes(
                                        "text-base font-bold leading-tight break-words line-clamp-2"
                                    )
                                    ui.label(p["lang_pair"]).classes(
                                        "text-[10px] font-black uppercase tracking-widest text-primary"
                                    )
                                ui.button(
                                    icon="delete",
                                    on_click=lambda e, pid=p["id"], fn=p["filename"], c=container: (
                                        _confirm_delete(pid, fn, c, client)
                                    ),
                                ).props("flat round dense size=sm color=grey-5").on("click.stop")

                            with ui.column().classes("w-full gap-1"):
                                ui.linear_progress(value=pct / 100, color="positive").props(
                                    "size=8px rounded"
                                ).classes("w-full")
                                with ui.row().classes("w-full justify-between items-center"):
                                    ui.label(
                                        f"{p['done']} / {p['total']} segments"
                                    ).classes("text-[10px] font-medium opacity-60")
                                    ui.label(f"{pct}%").classes("text-[10px] font-bold")
                            ui.label(
                                f"Saved {p['saved_at'][:16].replace('T', ' ')}"
                            ).classes("text-[9px] font-medium italic opacity-50")

                        # ── RIGHT: review/funnel status ──
                        from translate_core import review_manager as _rm
                        _reviews = _rm.list_reviews(original_project_id=p["id"])
                        _active = [r for r in _reviews if r.get("funnel_active") and not _is_review_expired(r)]
                        _inactive = [r for r in _reviews if not r.get("funnel_active") or _is_review_expired(r)]
                        _completed = [r for r in _reviews if r.get("reviewer_completed") and r.get("status") != "merged"]
                        _merged = [r for r in _reviews if r.get("status") == "merged"]

                        with ui.column().classes("w-64 shrink-0 gap-1.5 items-end text-right"):
                            # Review button always at top right
                            with ui.row().classes("gap-1 items-center"):
                                if _merged:
                                    ui.icon("check_circle", size="12px").props("color=positive")
                                    ui.label(f"{len(_merged)} merged").classes(
                                        "text-[9px] font-bold text-positive"
                                    )
                                elif _completed:
                                    ui.icon("task_alt", size="12px").props("color=positive")
                                    ui.label("Review done").classes(
                                        "text-[9px] font-bold text-positive"
                                    )
                                elif _active:
                                    ui.icon("sensors", size="12px").props("color=positive").classes("animate-pulse")
                                    ui.label("Funnel live").classes(
                                        "text-[9px] font-bold text-positive"
                                    )
                                elif _inactive:
                                    ui.icon("pause_circle", size="12px").props("color=amber-6")
                                    ui.label(f"{len(_inactive)} paused").classes(
                                        "text-[9px] font-medium text-amber-600"
                                    )
                                ui.button(
                                    icon="rate_review",
                                    on_click=lambda e, pid=p["id"]: ui.navigate.to(f"/review/{pid}"),
                                ).props("flat round dense size=sm color=grey-5").on("click.stop").tooltip("Review")

                            # Merged review details
                            if _merged:
                                for r in _merged[:1]:
                                    _rev = r.get("reviewer_name", "") or "unnamed"
                                    _rounds = r.get("round_trip_count", 0) + 1
                                    with ui.column().classes("w-full gap-0.5 items-end text-right"):
                                        with ui.row().classes("gap-1 items-center justify-end"):
                                            ui.icon("person", size="10px").props("color=grey-6")
                                            ui.label(_rev).classes("text-[9px] opacity-60")
                                        ui.label(f"Merged ({_rounds} round{'s' if _rounds != 1 else ''})").classes(
                                            "text-[9px] font-bold text-positive opacity-80"
                                        )

                            # Reviewer-completed details: timestamp
                            if _completed:
                                for r in _completed[:1]:
                                    _comp_at = r.get("reviewer_completed_at", "")
                                    _comp_short = _comp_at[:16].replace("T", " ") if _comp_at else ""
                                    _rev = r.get("reviewer_name", "") or "unnamed"
                                    with ui.column().classes("w-full gap-0.5 items-end text-right"):
                                        with ui.row().classes("gap-1 items-center justify-end"):
                                            ui.icon("person", size="10px").props("color=grey-6")
                                            ui.label(_rev).classes("text-[9px] opacity-60")
                                        if _comp_short:
                                            with ui.row().classes("gap-1 items-center justify-end"):
                                                ui.icon("event_available", size="10px").props("color=positive")
                                                ui.label(f"done {_comp_short}").classes("text-[9px] text-positive opacity-80")
                                        ui.label("Ready to merge").classes("text-[9px] font-bold text-positive")

                            # Active funnel details: reviewer, expiry, URL
                            if _active:
                                for r in _active[:2]:
                                    _exp = r.get("funnel_expires_at", "")
                                    _exp_short = _exp[:16].replace("T", " ") if _exp else "—"
                                    _rev = r.get("reviewer_name", "") or "unnamed"
                                    _url = f"{r.get('funnel_url', '')}/review/ext/{r['review_id']}"
                                    with ui.column().classes("w-full gap-0.5 items-end text-right"):
                                        with ui.row().classes("gap-1 items-center justify-end"):
                                            ui.icon("person", size="10px").props("color=grey-6")
                                            ui.label(_rev).classes("text-[9px] opacity-60")
                                        with ui.row().classes("gap-1 items-center justify-end"):
                                            ui.icon("schedule", size="10px").props("color=grey-6")
                                            ui.label(f"until {_exp_short}").classes("text-[9px] opacity-60")
                                        with ui.row().classes("w-full gap-1 items-center justify-end"):
                                            ui.label(_url).classes(
                                                "text-[9px] font-mono opacity-50 flex-1 min-w-0 text-right"
                                            ).style("word-break: break-all; white-space: normal;")
                                            ui.button(
                                                icon="content_copy",
                                                on_click=lambda e, t=_url: ui.run_javascript(
                                                    f"navigator.clipboard.writeText({json.dumps(t)})"
                                                ),
                                            ).props("flat round dense size=sm color=grey-6").on("click.stop").tooltip("Copy review link")
                            elif not _reviews:
                                ui.label("No reviews").classes(
                                    "text-[9px] italic opacity-40"
                                )

                            ui.icon("arrow_forward", size="14px").props("color=primary").classes("mt-auto")

                    # ── Billing card (separate, NOT clickable) ──
                    with ui.card().props("flat bordered").classes(
                        "shrink-0 p-3 rounded-2xl w-64 flex flex-col gap-2 justify-center"
                    ):
                        # Selection checkbox + BILLING header
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label("BILLING").classes(
                                "text-[8px] font-black uppercase tracking-[0.2em] opacity-50"
                            )
                            _pid = p["id"]
                            billing_state[_pid] = {"project": p, "rate": p.get("billing_rate"), "_checked": False}
                            _cb = ui.checkbox("Select", value=False).props(
                                "dense size=sm"
                            ).classes("text-[9px]")

                            def _on_check(e, _pid=_pid):
                                billing_state[_pid]["_checked"] = bool(e.value)
                                _update_selection_label()

                            _cb.on_value_change(_on_check)

                        with ui.row().classes("w-full justify-between items-end"):
                            with ui.column().classes("gap-0"):
                                ui.label(f"{p['chars_with']:,}").classes(
                                    "text-[11px] font-bold tabular-nums"
                                )
                                ui.label("chars w/ spaces").classes(
                                    "text-[7px] uppercase tracking-wider opacity-50"
                                )
                            with ui.column().classes("gap-0"):
                                ui.label(f"{p['chars_without']:,}").classes(
                                    "text-[11px] font-bold tabular-nums"
                                )
                                ui.label("chars no spaces").classes(
                                    "text-[7px] uppercase tracking-wider opacity-50"
                                )
                        with ui.row().classes("w-full items-baseline justify-between"):
                            ui.label("pages (÷1500)").classes(
                                "text-[8px] uppercase tracking-wider opacity-50"
                            )
                            ui.label(f"{p['pages']:.2f}").classes(
                                "text-sm font-black tabular-nums text-primary"
                            )
                        ui.separator().classes("opacity-30")
                        with ui.row().classes("w-full items-center gap-1"):
                            _saved_rate = p.get("billing_rate")
                            _rate_input = ui.number(
                                label="€/page",
                                value=_saved_rate,
                                min=0,
                                step=0.5,
                                format="%.2f",
                            ).props("outlined dense").classes("flex-1 text-xs")
                            _total_label = ui.label("").classes(
                                "text-base font-black tabular-nums text-positive"
                            )

                            # Initialise total from saved rate so it shows on page load
                            if _saved_rate:
                                _total_label.set_text(
                                    f"€{round(p['pages'] * float(_saved_rate), 2):,.2f}"
                                )
                            else:
                                _total_label.set_text("€0.00")

                            def _update_total(e, _pid=_pid, _pages=p["pages"], _lbl=_total_label):
                                val = getattr(e, "value", e)
                                try:
                                    rate = float(val) if val is not None else 0.0
                                except (TypeError, ValueError):
                                    rate = 0.0
                                total = round(_pages * rate, 2)
                                _lbl.set_text(f"€{total:,.2f}")
                                # Persist rate in shared state for export
                                billing_state[_pid]["rate"] = rate
                                # Save to project JSON so it survives restart
                                _save_billing_rate(_pid, rate if rate else None)

                            _rate_input.on_value_change(_update_total)



def _confirm_delete(project_id: str, filename: str, container: ui.column, client) -> None:
    """Show a confirmation dialog before deleting a project."""
    with ui.dialog() as dialog:
        with ui.card().classes("min-w-[420px]"):
            ui.label("Delete project?").classes("text-lg font-bold mb-2")
            ui.label(
                f"This will permanently delete \"{filename}\" and all its segments, "
                f"translations, and review history. This cannot be undone."
            ).classes("text-sm opacity-70 mb-4")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: dialog.close()).props("flat")
                ui.button(
                    "Delete permanently",
                    on_click=lambda: (
                        dialog.close(),
                        _do_delete(project_id, container, client),
                    ),
                ).props("color=negative unelevated")
    dialog.open()


def _do_delete(project_id: str, container: ui.column, client) -> None:
    """Execute the deletion and refresh the project list."""
    delete_project(project_id)
    ui.notify("Project deleted", type="warning", timeout=1200)
    render_project_list(container, client)


async def handle_new_upload(e, lang_pair: str):
    from nicegui import run

    name = e.file.name
    suffix = Path(name).suffix.lower()
    if suffix not in (".docx", ".pdf"):
        return ui.notify("DOCX or PDF files only", type="warning")

    project_id = str(uuid.uuid4())[:8]
    saved_path = PROJECTS_DIR / f"{project_id}{suffix}"
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    # Save uploaded file to disk (NiceGUI FileUpload.save writes directly,
    # no intermediate read-into-memory for large files).
    try:
        await e.file.save(saved_path)
    except Exception as ex:
        log.error("Error saving uploaded file: %s", ex)
        return ui.notify("Error saving file", type="negative")

    # ── Pipeline selection dialog ────────────────────────────────────────
    detected = _detect_pipeline(saved_path, suffix)
    default_ptype = "book_translation" if detected == "academic" else "article_translation"

    with ui.dialog() as dialog:
        with ui.card().classes("min-w-[420px]"):
            ui.label("Create Translation Project").classes("text-lg font-bold mb-2")
            pipeline_radio = ui.radio(
                {"academic": "Academic — book/article (footnote/endnote restructuring, restyled academic export)",
                 "simple": "Short document (preserve original formatting on export)"},
                value=detected,
            ).classes("w-full")
            ptype_select = ui.select(
                ["book_translation", "article_translation", "festival_programme", "exhibition_catalogue"],
                label="Project type (KG container)",
                value=default_ptype,
            ).classes("w-full mt-2")
            from translate_core.publisher_styles import get_style_options
            style_select = ui.select(
                get_style_options(),
                label="Publisher house style",
                value=config.DEFAULT_HOUSE_STYLE,
            ).classes("w-full mt-2")
            with ui.row().classes("w-full justify-end mt-4"):
                ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
                ui.button("Create project", on_click=lambda: dialog.submit(
                    (pipeline_radio.value, ptype_select.value, style_select.value)
                )).props("color=positive")
    result = await dialog
    if result is None:
        saved_path.unlink(missing_ok=True)
        ui.notify("Import cancelled", type="info")
        return
    pipeline, project_type, house_style = result

    from ui.components import busy_overlay

    async with busy_overlay("Parsing document…"):
        # ── Parse matrix: pipeline × suffix ──────────────────────────────────
        try:
            if pipeline == "simple" and suffix == ".docx":
                segments = await run.io_bound(_parse_docx, saved_path)
            elif pipeline == "simple" and suffix == ".pdf":
                if doc_parser is None:
                    return ui.notify("Document parser not initialized", type="negative")
                segments = await run.io_bound(_parse_pdf, doc_parser, saved_path, preprocess=False)
            elif pipeline == "academic" and suffix == ".docx":
                if doc_parser is None:
                    return ui.notify("Document parser not initialized", type="negative")
                segments = await run.io_bound(_parse_academic_docx, doc_parser, saved_path)
            else:  # academic + .pdf
                if doc_parser is None:
                    return ui.notify("Document parser not initialized", type="negative")
                segments = await run.io_bound(_parse_pdf, doc_parser, saved_path, preprocess=True)
        except Exception as ex:
            log.error("Parse error: %s", ex)
            return ui.notify("Failed to parse document", type="negative")

        if not segments:
            return ui.notify("No text extracted from document", type="warning")

        from translate_core.book_outline import build_segments_meta

        ws = {
            "project_id": project_id,
            "filename": name,
            "lang_pair": lang_pair,
            "project_type": project_type,
            "house_style": house_style,
            "active_index": 0,
            "segments": segments,
            "segments_meta": build_segments_meta(segments),
            "pipeline": pipeline,
        }

        await run.io_bound(save_project, ws)
    notify_msg = f"Created: {len(segments)} segments ({pipeline})"
    if pipeline == "academic" and doc_parser is not None and doc_parser.last_footnote_report:
        rpt = doc_parser.last_footnote_report
        if rpt.get("aligned"):
            notify_msg += f" — {rpt['defs']} footnotes, refs aligned"
        else:
            notify_msg += f" — {rpt['defs']} footnotes, {rpt['refs']} refs (misaligned)"
    ui.notify(notify_msg, type="positive")
    ui.navigate.to(f"/translate/{project_id}")


def _detect_pipeline(saved_path: Path, suffix: str) -> str:
    """Pre-select a pipeline based on file content. Never authoritative —
    the user confirms via the upload dialog."""
    try:
        from defusedxml import ElementTree as DefusedET
    except ImportError:
        DefusedET = ET
        log.warning("defusedxml not installed; falling back to stdlib ElementTree")
    if suffix == ".pdf":
        return "academic"
    # .docx — inspect the zip for footnote/endnote parts
    try:
        with zipfile.ZipFile(saved_path, "r") as zf:
            for part_name in ("word/footnotes.xml", "word/endnotes.xml"):
                if part_name not in zf.namelist():
                    continue
                raw = zf.read(part_name)
                root = DefusedET.fromstring(raw)
                ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                # The tag name inside footnotes.xml is w:footnote,
                # inside endnotes.xml is w:endnote.
                local = "footnote" if "footnote" in part_name else "endnote"
                for el in root.iter(f"{{{ns}}}{local}"):
                    # Accept if w:type attribute is absent (real note, not separator)
                    if el.get(f"{{{ns}}}type") is None:
                        return "academic"
    except Exception:
        pass  # Corrupt zip or missing parts — fall through
    # Paragraph count heuristic
    try:
        import docx as _docx
        if len(_docx.Document(str(saved_path)).paragraphs) > 200:
            return "academic"
    except Exception:
        pass
    return "simple"


def _docx_paragraph_to_markdown(para) -> str:
    """Convert a python-docx paragraph to markdown text with *italic* /
    **bold** / ***bold-italic*** emphasis markers around runs whose font
    flags indicate emphasis.

    This is the simple-pipeline counterpart of
    DocumentParser.docx_to_markdown (academic) and
    _pdf_dict_to_markdown_text (PDF). It walks paragraph.runs and emits
    markdown emphasis markers so the translator can see and correct
    italics/bolds on the fly — without restructuring the document.

    Adjacent runs with identical emphasis flags are coalesced before
    wrapping so the output stays compact: *one span*, not *one* *span*.
    """
    if not para.runs:
        return para.text
    # Collect (text, italic, bold) tuples, coalescing adjacent runs with
    # identical (italic, bold) flags so we don't emit *a**b* when one
    # italic run was split into two by Word's XML serializer.
    coalesced: list[tuple[str, bool, bool]] = []
    for run in para.runs:
        txt = run.text
        if not txt:
            continue
        it = bool(run.italic)
        bd = bool(run.bold)
        if coalesced and coalesced[-1][1] == it and coalesced[-1][2] == bd:
            coalesced[-1] = (coalesced[-1][0] + txt, it, bd)
        else:
            coalesced.append((txt, it, bd))
    parts: list[str] = []
    for txt, it, bd in coalesced:
        if it and bd:
            parts.append(f"***{txt}***")
        elif bd:
            parts.append(f"**{txt}**")
        elif it:
            parts.append(f"*{txt}*")
        else:
            parts.append(txt)
    return "".join(parts)


def _parse_docx(path: Path) -> list[dict]:
    """Extract paragraphs from a DOCX file for the simple pipeline.

    Preserves docx_para_idx for template-based export. Emits *italic* /
    **bold** / ***bold-italic*** markdown markers around runs whose font
    flags indicate emphasis, so the translator can see and correct emphasis
    on the fly. Runs in a thread pool.
    """
    import docx as _docx
    from translate_core.book_outline import split_paragraphs
    doc = _docx.Document(str(path))
    segments = []
    for i, p in enumerate(doc.paragraphs):
        txt = _docx_paragraph_to_markdown(p).strip()
        if txt:
            for chunk in split_paragraphs(txt, max_chars=config.SEGMENT_MAX_CHARS):
                segments.append({
                    "id": len(segments),
                    "source": chunk,
                    "target": "",
                    "status": "pending",
                    "docx_para_idx": i,
                })
    return segments


def _parse_academic_docx(parser: "DocumentParser", path: Path) -> list[dict]:
    """Parse a DOCX via the academic pipeline (docx_to_markdown).
    No docx_para_idx — segments are restyled on export. Runs in a thread pool."""
    from translate_core.book_outline import split_paragraphs
    md = parser.docx_to_markdown(path)
    segments = []
    for txt in split_paragraphs(md, max_chars=config.SEGMENT_MAX_CHARS):
        segments.append({"id": len(segments), "source": txt, "target": "", "status": "pending"})
    return segments


def _parse_pdf(parser: "DocumentParser", path: Path, *, preprocess: bool = True) -> list[dict]:
    """Convert PDF to markdown and split into segments. Runs in a thread pool.
    preprocess=True applies endnote conversion + list remap (academic pipeline).
    preprocess=False skips them (simple pipeline — short documents).

    Captures a paragraph-formatting manifest from the PDF (font sizes for
    heading detection, block alignment/indent) and aligns it to the resulting
    segments as additive keys (pdf_para_idx, heading_level, para_align,
    para_indent_in, is_blockquote). The manifest is purely metadata; it never
    re-splits or re-merges segments — the translator's segmentation is frozen.
    """
    from translate_core.book_outline import split_paragraphs
    from translate_core.pdf_format_capture import (
        capture_paragraph_manifest,
        attach_manifest_to_segments,
    )
    md_text, _ = parser.to_markdown_with_meta(path, preprocess=preprocess)
    segments: list[dict] = []
    for txt in split_paragraphs(md_text, max_chars=config.SEGMENT_MAX_CHARS):
        segments.append({"id": len(segments), "source": txt, "target": "", "status": "pending"})
    # Attach the PDF paragraph manifest as additive metadata (never alters
    # source/target/status/id). Unmatched segments (footnote defs, edge
    # cases) simply lack pdf_para_idx and fall back to one-paragraph-per-segment
    # at export — the current behavior.
    try:
        manifest = capture_paragraph_manifest(path)
        if manifest.paragraphs:
            attach_manifest_to_segments(segments, manifest)
    except Exception as ex:
        log.warning("pdf_format_capture failed (non-fatal): %s", ex)
    return segments


# Register the translation workspace page. Importing ui.workspace is a
# side-effect import: it runs the @ui.page("/translate/{project_id}") decorator.
# The alias avoids shadowing NiceGUI's `ui` and the explicit noqa silences
# both the import-position and unused-name checks.
from ui import workspace as _zen_workspace  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
from ui import kg_editor as _kg_editor  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
from ui import aligner as _zen_aligner  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
from ui import review as _zen_review  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
_ = _zen_workspace, _kg_editor, _zen_aligner, _zen_review  # mark the bindings as deliberately consumed


# Publish module-level functions to app_state once they're all defined.
# These do NOT depend on init_resources running first.
app_state.save_project = save_project
app_state.load_project = load_project
app_state.save_pair_to_tm = save_pair_to_tm
app_state.parse_lang_pair = parse_lang_pair


def apply_colors():
    ui.colors(
        primary="#22c55e",
        secondary="#334155",
        positive="#22c55e",
        accent="#3b82f6",
        negative="#ef4444",
    )


# Wire apply_colors as well, now that it's defined.
app_state.apply_colors = apply_colors

if __name__ in {"__main__", "__mp_main__"}:
    # storage_secret is required for app.storage.user (dark mode persistence).
    # Any non-empty string works for a single-user desktop app.
    ui.run(
        title="Bel Translation Suite",
        favicon="✨",
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=8080,
        show=True,
        storage_secret=config.STORAGE_SECRET,
        # kg.save on the 97MB KG can legitimately block a worker thread
        # for 1-3 s during a confirm. NiceGUI's default ping_timeout =
        # max(reconnect_timeout * 0.4, 2) = 2 s, which drops the page on
        # any such call. Widen the window so the editor survives it.
        reconnect_timeout=30.0,
    )
