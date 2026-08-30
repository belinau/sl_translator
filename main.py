# main.py
#
# Bel Translation Suite — Main Application Entrypoint
# Fully integrated with advanced document pre-processing and compiled DOCX exports
#

import os
import re
import json
import sys
import logging
import uuid
from datetime import datetime
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Tuple


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
    from translate_core.client_store import ClientStore
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
                    "client_id": data.get("client_id"),
                    "responsible_person": data.get("responsible_person", ""),
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
    for k in ("pipeline", "project_type", "segments_meta", "house_style",
              "billing_rate", "client_id", "responsible_person"):
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

def _save_billing_fields(project_id: str, fields: dict) -> None:
    """Patch billing fields (client_id, responsible_person, billing_rate)
    into a project JSON without rewriting segments."""
    validate_project_id(project_id)
    path = PROJECTS_DIR / f"{project_id}.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in fields.items():
            data[k] = v
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
                ui.button(icon="receipt_long", on_click=lambda: ui.navigate.to("/invoices")).props("flat round dense color=white").tooltip("Invoices")
                ui.button(icon="contacts", on_click=lambda: ui.navigate.to("/clients")).props("flat round dense color=white").tooltip("Clients")
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
                ).classes("text-[10px]").tooltip("Export selected projects as PDF billing report")
                _xlsx_btn = ui.button("XLSX", icon="table_chart").props(
                    "flat dense no-caps color=primary size=sm"
                ).classes("text-[10px]").tooltip("Export selected projects as XLSX billing report")
                ui.separator().props("vertical").classes("h-6 mx-1")
                _racun_btn = ui.button("Račun", icon="description").props(
                    "flat dense no-caps color=primary size=sm"
                ).classes("text-[10px]").tooltip("Generate plain printed invoice (XLSX + PDF)")
                _eracun_btn = ui.button("e-Račun", icon="electrical_services").props(
                    "flat dense no-caps color=primary size=sm"
                ).classes("text-[10px]").tooltip("Generate eSLOG electronic invoice (XML + PDF)")
                _other_btn = ui.button("Other", icon="post_add").props(
                    "flat dense no-caps color=primary size=sm"
                ).classes("text-[10px]").tooltip(
                    "Printed invoice for other services — add line items manually"
                )
                _other_eracun_btn = ui.button("Other e-Račun", icon="post_add").props(
                    "flat dense no-caps color=blue size=sm"
                ).classes("text-[10px]").tooltip(
                    "e-Račun for other services — add line items manually"
                )

        def _get_selected_projects() -> list:
            """Collect billing dicts for all checked projects."""
            out = []
            for pid, st in billing_state.items():
                if st.get("_checked"):
                    p = st["project"]
                    out.append({
                        "id": pid,
                        "filename": p["filename"],
                        "lang_pair": p["lang_pair"],
                        "chars_with": p["chars_with"],
                        "chars_without": p["chars_without"],
                        "pages": p["pages"],
                        "client_id": p.get("client_id"),
                        "responsible_person": p.get("responsible_person", ""),
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

        # ── Invoice dialog (shared by Račun + e-Račun) ──
        def _open_invoice_dialog(invoice_type: str, allow_empty: bool = False):
            """Open the invoice generation dialog.

            invoice_type: "plain" (XLSX+PDF) or "eracun" (eSLOG XML+PDF).
            allow_empty: no pre-selected documents — line items added manually
            (for invoices for services not tied to translation projects).
            """
            sel = _get_selected_projects()
            if not sel and not allow_empty:
                ui.notify("Select at least one project first.", type="warning")
                return
            from datetime import date, timedelta
            from decimal import Decimal
            import config as cfg

            store = ClientStore()
            clients = store.list_clients_brief()
            if not clients:
                ui.notify("Add a client first on the Clients page.", type="warning")
                return

            # Pre-select the client shared by the first selected projects.
            pre_client = next((s["client_id"] for s in sel if s["client_id"]), clients[0]["id"])

            today = date.today()

            with ui.dialog().props("persistent") as _dlg:
                with ui.card().classes("min-w-[720px] max-w-[900px] p-6 gap-4"):
                    title_txt = "e-Račun (eSLOG)" if invoice_type == "eracun" else "Račun (printed)"
                    ui.label(title_txt).classes("text-lg font-bold")

                    _line_widgets: list[dict] = []
                    _current_rec: list = [None]
                    _grand_total_lbl = ui.label("€0.00")

                    def _update_grand_total():
                        total = 0.0
                        for lw in _line_widgets:
                            q = float(lw["qty"].value or 0)
                            pr = float(lw["price"].value or 0)
                            total += q * pr
                        _grand_total_lbl.set_text(f"€{total:,.2f}")

                    # ── Client / approver ──
                    with ui.row().classes("w-full gap-4"):
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Client").classes("text-[10px] font-bold uppercase opacity-60")
                            _client_sel = ui.select(
                                {c["id"]: c["name"] for c in clients},
                                value=pre_client,
                            ).props("outlined dense").classes("w-full")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Approved by (organisation)").classes(
                                "text-[10px] font-bold uppercase opacity-60"
                            )
                            _approver_sel = ui.select({}, value=None).props(
                                "outlined dense"
                            ).classes("w-full")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Reference doc type").classes(
                                "text-[10px] font-bold uppercase opacity-60"
                            )
                            _doc_type = ui.select(
                                ["Pogodba", "Naročilnica", "Dnevni red", "Dogovor"],
                                value="Pogodba",
                            ).props("outlined dense").classes("w-full")

                    # ── Dates ──
                    with ui.row().classes("w-full gap-4"):
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Issue date").classes("text-[10px] font-bold uppercase opacity-60")
                            _issue_date = ui.date(value=today.isoformat()).props("outlined dense").classes("w-full")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Due date").classes("text-[10px] font-bold uppercase opacity-60")
                            _due_date = ui.date(value=(today + timedelta(days=30)).isoformat()).props("outlined dense").classes("w-full")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Service from").classes("text-[10px] font-bold uppercase opacity-60")
                            _svc_from = ui.date(value=today.isoformat()).props("outlined dense").classes("w-full")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Service to").classes("text-[10px] font-bold uppercase opacity-60")
                            _svc_to = ui.date(value=today.isoformat()).props("outlined dense").classes("w-full")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Contract/PO date").classes("text-[10px] font-bold uppercase opacity-60")
                            _doc_date = ui.date(value=today.isoformat()).props("outlined dense").classes("w-full")

                    # ── Order info (prefilled from client defaults) ──
                    with ui.row().classes("w-full gap-4"):
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Order no.").classes("text-[10px] font-bold uppercase opacity-60")
                            _order_no = ui.input(value="dogovor").props("outlined dense").classes("w-full")
                        with ui.column().classes("flex-1 gap-1"):
                            ui.label("Project code").classes("text-[10px] font-bold uppercase opacity-60")
                            _proj_code = ui.input(value="/").props("outlined dense").classes("w-full")

                    # Convert project pair ('en->sl') to dropdown code ('ENG>SLO').
                    _code_map = {"SL": "SLO", "EN": "ENG", "HR": "HRV", "SR": "SRB",
                                 "SRP": "SRB", "ENG": "ENG", "SLO": "SLO", "HRV": "HRV"}

                    def _pair_code(pair: str) -> str:
                        if not pair:
                            return "ENG>SLO"
                        src, _, tgt = pair.strip().replace("->", ">").upper().partition(">")
                        tgt0 = tgt.split()[0] if tgt else ""
                        s = _code_map.get(src, src or "ENG")
                        t = _code_map.get(tgt0, tgt0 or "SLO")
                        exact, base = f"{s}>{t}", f"{s}>{t}"
                        opts = cfg.INVOICE_LANG_PAIRS
                        if exact in opts:
                            return exact
                        for o in opts:
                            if o.startswith(base + " ") or o == base:
                                return o
                        return opts[0]

                    def _recalc_qty(lw):
                        unit = lw["unit"].value or "stran"
                        cw = lw["chars_with"]
                        cwo = lw["chars_without"]
                        if unit == "pavšal":
                            lw["qty"].value = 1.0
                        elif unit == "stran":
                            lw["qty"].value = round(cwo / 1500, 2)
                        elif unit == "znak":
                            lw["qty"].value = float(cwo)
                        elif unit == "avtorska pola":
                            lw["qty"].value = round(cw / cfg.AUTHORIAL_SHEET_CHARS, 4)

                    def _upd_line(e, lw):
                        q = float(lw["qty"].value or 0)
                        pr = float(lw["price"].value or 0)
                        lw["total_lbl"].set_text(f"€{q * pr:,.2f}")
                        _update_grand_total()

                    def _refill_rate(lw):
                        rec = _current_rec[0]
                        _recalc_qty(lw)
                        if rec:
                            rate = rec.get_rate(
                                lw["svc"].value or "Prevod",
                                lw["pair"].value or "ENG>SLO",
                                lw["unit"].value or "stran",
                            )
                            if rate is not None:
                                lw["price"].value = float(rate)
                    with ui.scroll_area().classes("w-full h-40 border rounded"):
                        _line_rows_container = ui.column().classes("w-full gap-1 p-1")
                    def _make_line_row(proj: dict | None = None, with_person: str = ""):
                        """Create one editable line item row. Returns its widget dict."""
                        with _line_rows_container:
                            with ui.row().classes("w-full gap-1 items-center") as row_el:
                                _svcw = ui.select(cfg.INVOICE_SERVICE_TYPES, value="Prevod").props("outlined dense").classes("flex-[2] text-[10px]")
                                _pairw = ui.select(cfg.INVOICE_LANG_PAIRS, value=_pair_code(proj["lang_pair"]) if proj else "ENG>SLO").props("outlined dense").classes("flex-[2] text-[10px]")
                                _unitw = ui.select(cfg.INVOICE_UNITS, value="stran").props("outlined dense").classes("w-28 text-[10px]")
                                _qtyw = ui.number(value=round(proj["pages"], 2) if proj else 1.0, min=0, step=0.01, format="%.2f").props("outlined dense").classes("w-20 text-[10px]")
                                _pricew = ui.number(value=0, min=0, step=0.5, format="%.2f").props("outlined dense").classes("w-20 text-[10px]")
                                _desc_val = proj["filename"] if proj else ""
                                # Person is NOT appended here — full_description
                                # property on InvoiceLineItem handles that.
                                _descw = ui.input(value=_desc_val).props("outlined dense").classes("flex-[3] text-[10px]")
                                _rem = ui.button(icon="delete").props("flat round dense size=sm color=negative")
                                _tl = ui.label("€0.00").classes("text-[10px] font-bold tabular-nums text-positive w-16 text-right")
                                lw = {
                                    "svc": _svcw, "pair": _pairw, "unit": _unitw,
                                    "qty": _qtyw, "price": _pricew, "desc": _descw,
                                    "total_lbl": _tl,
                                    "chars_with": proj.get("chars_with", 0) if proj else 0,
                                    "chars_without": proj.get("chars_without", 0) if proj else 0,
                                    "pages": proj.get("pages", 0.0) if proj else 0.0,
                                    "responsible_person": with_person,
                                    "__rem": _rem,
                                    "__row": row_el,
                                }

                                def _upd(e, l=lw):
                                    _upd_line(e, l)

                                def _rf(e, l=lw):
                                    _refill_rate(l)

                                _qtyw.on_value_change(_upd)
                                _pricew.on_value_change(_upd)
                                _svcw.on_value_change(_rf)
                                _pairw.on_value_change(_rf)
                                _unitw.on_value_change(_rf)
                        return lw

                    for proj in sel:
                        lw = _make_line_row(proj, with_person=proj.get("responsible_person") or "")
                        _line_widgets.append(lw)

                    def _add_empty_row():
                        lw = _make_line_row(None)
                        _line_widgets.append(lw)
                        rec = _current_rec[0]
                        if rec:
                            rate = rec.get_rate("Prevod", "ENG>SLO", "stran")
                            if rate is not None:
                                lw["price"].value = float(rate)
                        _reg_del(lw)
                        _upd_line(None, lw)

                    def _reg_del(lw):
                        """Wire the row's delete button."""
                        def _del():
                            if lw in _line_widgets:
                                _line_widgets.remove(lw)
                            row_el = lw.get("__row")
                            if row_el:
                                try:
                                    row_el.delete()
                                except Exception:
                                    pass
                            _update_grand_total()
                        lw["__rem"].on_click(lambda: _del())

                    for lw in list(_line_widgets):
                        _reg_del(lw)

                    with ui.row().classes("w-full justify-between items-center"):
                        ui.button("Add line item", icon="add", on_click=_add_empty_row).props(
                            "flat dense no-caps color=primary size=sm"
                        ).classes("text-[10px]")

                        def _add_predplacilo():
                            """Add a 'Že prejeto predplačilo' deduction line
                            referencing a past invoice for the selected client."""
                            cid = _client_sel.value
                            if not cid:
                                ui.notify("Select a client first.", type="warning")
                                return
                            past = [i for i in store.list_invoices()
                                    if i.get("client_id") == cid
                                    and i.get("invoice_number", "") != ""]
                            if not past:
                                ui.notify("No past invoices for this client.", type="warning")
                                return
                            with ui.dialog() as pdlg:
                                with ui.card().classes("min-w-[420px] p-4 gap-3"):
                                    ui.label("Select invoice to deduct as predplačilo").classes(
                                        "text-sm font-bold")
                                    opts = {i["invoice_number"]: (
                                        f"{i['invoice_number']} — {i['total']} EUR"
                                    ) for i in past}
                                    _inv_sel = ui.select(opts, value=None).props("outlined dense").classes("w-full")

                                    def _do_add():
                                        num = _inv_sel.value
                                        if not num:
                                            return
                                        prev = next(i for i in past if i["invoice_number"] == num)
                                        amt = float(Decimal(prev.get("total", "0")))
                                        lw = _make_line_row(None)
                                        lw["svc"].value = "Predplačilo"
                                        lw["pair"].value = "ENG>SLO"
                                        lw["unit"].value = "pavšal"
                                        lw["qty"].value = 1.0
                                        lw["price"].value = -amt
                                        lw["desc"].value = f"Že prejeto predplačilo (Račun {num})"
                                        lw["responsible_person"] = ""
                                        _line_widgets.append(lw)
                                        _reg_del(lw)
                                        _upd_line(None, lw)
                                        pdlg.close()
                                        ui.notify(f"Added predplačilo: -{amt} EUR", type="positive")

                                    with ui.row().classes("w-full justify-end gap-2"):
                                        ui.button("Cancel", on_click=lambda: pdlg.close()).props("flat")
                                        ui.button("Add deduction", icon="remove", on_click=_do_add).props(
                                            "unelevated color=negative")
                            pdlg.open()

                        ui.button("Add predplačilo", icon="remove", on_click=_add_predplacilo).props(
                            "flat dense no-caps color=warning size=sm"
                        ).classes("text-[10px]").tooltip(
                            "Add a deduction line for an advance already received"
                        )

                    with ui.row().classes("w-full justify-end items-baseline gap-2"):
                        ui.label("Total").classes("text-[10px] uppercase opacity-50")
                        _grand_total_lbl.classes("text-base font-black tabular-nums text-positive")

                    # ── Client change: approver, defaults, rates, quantities ──
                    _current_rec: list = [None]

                    def _on_client_change(e):
                        cid = e.value
                        rec = store.get_client(cid) if cid else None
                        if not rec:
                            return
                        _current_rec[0] = rec
                        persons = [p["name"] for p in rec.responsible_persons]
                        _approver_sel.set_options(
                            {p: p for p in persons},
                            value=persons[0] if persons else None,
                        )
                        _order_no.value = rec.default_order_number or "dogovor"
                        _proj_code.value = rec.default_project_code or "/"
                        _doc_type.value = rec.default_doc_type or "Pogodba"
                        for lw in _line_widgets:
                            # Person from the project card, if not already in description
                            _refill_rate(lw)
                        _update_grand_total()

                    _client_sel.on_value_change(_on_client_change)
                    _on_client_change(type("E", (), {"value": pre_client})())

                    # ── Buttons ──
                    with ui.row().classes("w-full justify-end gap-2 mt-4"):
                        ui.button("Cancel", on_click=lambda: _dlg.close()).props("flat color=grey")

                        def _generate():
                            from translate_core.invoice_models import InvoiceData, InvoiceLineItem
                            from translate_core.invoice_plain import (
                                generate_plain_invoice_xlsx, generate_plain_invoice_pdf,
                            )
                            from translate_core.invoice_eslog import (
                                generate_eslog_xml, generate_eslog_pdf, validate_eslog_xml,
                            )

                            cid = _client_sel.value
                            rec = store.get_client(cid) if cid else None
                            if not rec:
                                ui.notify("Select a client.", type="warning")
                                return
                            if invoice_type == "eracun":
                                if not rec.use_eracun:
                                    ui.notify("This client is not flagged for e-račun.", type="warning")
                                    return
                                if not rec.iban:
                                    ui.notify("Client IBAN is required for e-račun.", type="warning")
                                    return
                            approver = _approver_sel.value or ""
                            if invoice_type == "eracun" and not approver:
                                ui.notify("Organisation approver is required for e-račun.", type="warning")
                                return

                            items = []
                            for lw in _line_widgets:
                                q = Decimal(str(lw["qty"].value or 0))
                                pr = Decimal(str(lw["price"].value or 0))
                                if q > 0 and pr > 0:
                                    items.append(InvoiceLineItem(
                                        description=lw["desc"].value or "",
                                        service_type=lw["svc"].value or "Prevod",
                                        lang_pair=lw["pair"].value or "ENG>SLO",
                                        unit=lw["unit"].value or "stran",
                                        quantity=q,
                                        unit_price=pr,
                                        responsible_person=lw.get("responsible_person", ""),
                                    ))
                            if not items:
                                ui.notify("No valid line items — check quantities and prices.", type="warning")
                                return

                            issue_d = date.fromisoformat(_issue_date.value)
                            due_d = date.fromisoformat(_due_date.value)
                            svc_f = date.fromisoformat(_svc_from.value)
                            svc_t = date.fromisoformat(_svc_to.value)

                            inv_num = store.next_invoice_number(issue_d.year)

                            inv_data = InvoiceData(
                                invoice_number=inv_num,
                                issue_date=issue_d,
                                due_date=due_d,
                                service_date_from=svc_f,
                                service_date_to=svc_t,
                                client=rec,
                                issuer=cfg.ISSUER,
                                line_items=items,
                                approver=approver,
                                responsible_person="",
                                order_number=_order_no.value or "dogovor",
                                project_code=_proj_code.value or "/",
                                legal_notes=cfg.INVOICE_LEGAL_NOTES,
                            )

                            # e-račun: hard-gate on schema validation before download
                            xml_bytes = None
                            if invoice_type == "eracun":
                                xml_bytes = generate_eslog_xml(inv_data)
                                ok, err = validate_eslog_xml(xml_bytes)
                                if not ok:
                                    ui.notify(
                                        "e-SLOG validation failed — not generated:\n"
                                        + err[:400],
                                        type="negative", close_button="OK",
                                    )
                                    return

                            # ── Save to invoice archive folder ──
                            out_dir = cfg.INVOICE_OUTPUT_DIR
                            out_dir.mkdir(parents=True, exist_ok=True)
                            pdf_path = xml_path = xlsx_path = None
                            try:
                                if invoice_type == "plain":
                                    _xlsx = generate_plain_invoice_xlsx(inv_data)
                                    xlsx_path = str(out_dir / f"Racun_{inv_num}.xlsx")
                                    (out_dir / f"Racun_{inv_num}.xlsx").write_bytes(_xlsx)
                                    try:
                                        pdf_path = str(out_dir / f"Racun_{inv_num}.pdf")
                                        (out_dir / f"Racun_{inv_num}.pdf").write_bytes(
                                            generate_plain_invoice_pdf(_xlsx))
                                    except RuntimeError as ex:
                                        pdf_path = None
                                        ui.notify(f"XLSX archived; PDF skipped ({ex})", type="warning")
                                else:
                                    xml_path = str(out_dir / f"Racun_{inv_num}.xml")
                                    (out_dir / f"Racun_{inv_num}.xml").write_bytes(xml_bytes)
                                    pdf_path = str(out_dir / f"Racun_{inv_num}.pdf")
                                    (out_dir / f"Racun_{inv_num}.pdf").write_bytes(
                                        generate_eslog_pdf(inv_data))
                            except Exception as ex:
                                log.exception("archive write failed")
                                ui.notify(f"Archive write failed: {ex}", type="negative")

                            # Record (encrypted at rest)
                            store.record_invoice({
                                "invoice_number": inv_num,
                                "issue_date": issue_d.isoformat(),
                                "due_date": due_d.isoformat(),
                                "service_date_from": svc_f.isoformat(),
                                "service_date_to": svc_t.isoformat(),
                                "invoice_type": invoice_type,
                                "client_id": cid,
                                "responsible_person": approver,
                                "approver": approver,
                                "order_number": _order_no.value or "",
                                "project_code": _proj_code.value or "",
                                "doc_type": _doc_type.value or "Pogodba",
                                "doc_date": _doc_date.value or "",
                                "total": str(inv_data.total),
                                "pdf_path": pdf_path,
                                "xml_path": xml_path,
                                "xlsx_path": xlsx_path,
                                "line_items": [
                                    {"desc": li.description, "qty": str(li.quantity),
                                     "price": str(li.unit_price), "unit": li.unit,
                                     "service_type": li.service_type,
                                     "lang_pair": li.lang_pair,
                                     "responsible_person": li.responsible_person}
                                    for li in items
                                ],
                            })

                            # Browser downloads for immediate use
                            try:
                                if invoice_type == "plain":
                                    ui.download(_xlsx,
                                                filename=f"Racun_{inv_num}.xlsx")
                                    if pdf_path:
                                        ui.download(Path(pdf_path).read_bytes(),
                                                    filename=f"Racun_{inv_num}.pdf")
                                    ui.notify(
                                        f"Račun {inv_num} generated — archived in {out_dir}",
                                        type="positive")
                                else:
                                    ui.download(xml_bytes, filename=f"Racun_{inv_num}.xml")
                                    if pdf_path:
                                        ui.download(Path(pdf_path).read_bytes(),
                                                    filename=f"Racun_{inv_num}.pdf")
                                    ui.notify(
                                        f"e-Račun {inv_num} generated (XSD-validated) — archived in {out_dir}",
                                        type="positive")
                            except Exception as ex:
                                log.exception("invoice download failed")
                                ui.notify(f"Files archived, but download failed: {ex}", type="warning")

                            _dlg.close()

                        ui.button("Generate", icon="send", on_click=_generate).props(
                            "unelevated color=positive"
                        )

            _dlg.open()

        _racun_btn.on("click", lambda: _open_invoice_dialog("plain"))
        _eracun_btn.on("click", lambda: _open_invoice_dialog("eracun"))
        _other_btn.on("click", lambda: _open_invoice_dialog("plain", allow_empty=True))
        _other_eracun_btn.on("click", lambda: _open_invoice_dialog("eracun", allow_empty=True))

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
                        "shrink-0 p-3 rounded-2xl w-72 flex flex-col gap-2 justify-center overflow-hidden"
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
                        # ── Client + responsible person (rates come from client) ──
                        _client_store = ClientStore()
                        _clients_brief = _client_store.list_clients_brief()
                        _client_opts = {c["id"]: c["name"] for c in _clients_brief}

                        with ui.row().classes("w-full items-center gap-1 min-w-0"):
                            _client_sel = ui.select(
                                _client_opts,
                                value=p.get("client_id") or None,
                                label="Client",
                            ).props("outlined dense").classes("flex-1 min-w-0 text-[10px]")
                            _person_sel = ui.select(
                                [], value=None,
                                label="Responsible",
                            ).props("outlined dense").classes("flex-1 min-w-0 text-[10px]")
                            _add_person_btn = ui.button(icon="person_add").props(
                                "flat round dense size=sm color=primary"
                            ).tooltip("Add new responsible person for this project")

                            def _persist_billing(_pid, **fields):
                                _save_billing_fields(_pid, fields)

                            def _on_card_client_change(e, _pid=_pid, _p=p, _csel=_client_sel, _psel=_person_sel, _store=_client_store):
                                _cid = _csel.value
                                rec = _store.get_client(_cid) if _cid else None
                                _persons_opts = {}
                                if rec:
                                    _persons_opts = {pp["name"]: pp["name"] for pp in rec.responsible_persons}
                                # Preserve a previously-saved person even if not in client list
                                _saved = _p.get("responsible_person") or ""
                                if _saved and _saved not in _persons_opts:
                                    _persons_opts[_saved] = _saved
                                _psel.set_options(
                                    _persons_opts,
                                    value=_saved or (
                                        next(iter(_persons_opts.values()), None)
                                    ),
                                )
                                _persist_billing(_pid, client_id=_cid)

                            def _on_card_person_change(e, _pid=_pid, _psel=_person_sel):
                                _persist_billing(_pid, responsible_person=e.value or "")

                            def _on_add_person(_pid=_pid, _csel=_client_sel, _psel=_person_sel, _store=_client_store):
                                """Add a new responsible person to the selected client.

                                Captured by value (default args) to avoid the Python
                                closure-in-loop bug where all closures would share
                                the last iteration's _client_sel.
                                """
                                _cid = _csel.value
                                if not _cid:
                                    ui.notify("Select a client first.", type="warning")
                                    return
                                _rec = _store.get_client(_cid)
                                if not _rec:
                                    return

                                with ui.dialog() as p_dlg:
                                    with ui.card().classes("min-w-[360px] p-4 gap-3"):
                                        ui.label(f"Add person to {_rec.name}").classes("text-sm font-bold")
                                        _pn = ui.input("Name").props("outlined dense").classes("w-full")
                                        _pr = ui.input("Role (optional)").props("outlined dense").classes("w-full")
                                        with ui.row().classes("w-full justify-end gap-2"):
                                            ui.button("Cancel", on_click=lambda: p_dlg.close()).props("flat")

                                            def _save_person():
                                                name = _pn.value.strip()
                                                if not name:
                                                    ui.notify("Name is required.", type="warning")
                                                    return
                                                role = _pr.value.strip()
                                                # 1. Add to client's person list (persists, reusable)
                                                existing = list(_rec.responsible_persons)
                                                if not any(p["name"] == name for p in existing):
                                                    existing.append({"name": name, "role": role})
                                                    # Rebuild data dict from current record + new person
                                                    _client_data = {
                                                        "name": _rec.name,
                                                        "address": _rec.address,
                                                        "postal_code": _rec.postal_code,
                                                        "city": _rec.city,
                                                        "country": _rec.country,
                                                        "country_code": _rec.country_code,
                                                        "vat_id": _rec.vat_id,
                                                        "vat_obliged": _rec.vat_obliged,
                                                        "iban": _rec.iban,
                                                        "bic": _rec.bic,
                                                        "bank_name": _rec.bank_name,
                                                        "maticna": _rec.maticna,
                                                        "account_holder": _rec.account_holder,
                                                        "default_order_number": _rec.default_order_number,
                                                        "default_project_code": _rec.default_project_code,
                                                        "default_doc_type": _rec.default_doc_type,
                                                        "default_doc_ref": _rec.default_doc_ref,
                                                        "use_eracun": _rec.use_eracun,
                                                        "rates": {k: str(v) for k, v in _rec.rates.items()},
                                                        "responsible_persons": existing,
                                                    }
                                                    _store.update_client(_cid, _client_data)
                                                # 2. Update the dropdown to show all client persons + select new one
                                                all_persons = {p["name"]: p["name"] for p in existing}
                                                _psel.set_options(all_persons, value=name)
                                                # 3. Persist to this project
                                                _persist_billing(_pid, responsible_person=name)
                                                p_dlg.close()
                                                ui.notify(f"Added {name} to {_rec.name} and selected for this project", type="positive")

                                            ui.button("Add", icon="add", on_click=_save_person).props(
                                                "unelevated color=positive"
                                            )
                                p_dlg.open()

                            _client_sel.on_value_change(_on_card_client_change)
                            _person_sel.on_value_change(_on_card_person_change)
                            _add_person_btn.on("click", lambda _, fn=_on_add_person: fn())

                            # Populate persons: always include the project's saved
                            # responsible_person (even if no client, or the person was
                            # added on-the-fly and isn't in the client's list).
                            _saved_person = p.get("responsible_person") or ""
                            _card_rec = _client_store.get_client(p["client_id"]) if p.get("client_id") else None
                            _persons = {}
                            if _card_rec:
                                _persons = {pp["name"]: pp["name"] for pp in _card_rec.responsible_persons}
                            if _saved_person and _saved_person not in _persons:
                                _persons[_saved_person] = _saved_person
                            if _persons:
                                _person_sel.set_options(_persons, value=_saved_person or None)
                            _rate = _card_rec.get_rate("Prevod", p["lang_pair"], "stran") if _card_rec else None
                            _total = round(p["pages"] * float(_rate), 2) if _rate is not None else 0.0

                        with ui.row().classes("w-full items-baseline justify-between"):
                            ui.label("total (client rate)").classes(
                                "text-[8px] uppercase tracking-wider opacity-50"
                            )
                            ui.label(f"€{_total:,.2f}").classes(
                                "text-base font-black tabular-nums text-positive"
                            )



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


# ===========================================================================
# PAGE /clients — Client management
# ===========================================================================
@ui.page("/clients")
def page_clients():
    from translate_core.client_store import ClientStore

    apply_colors()
    from ui import settings as ui_settings
    dm = ui_settings.install_dark_mode()
    ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")

    store = ClientStore()

    with ui.column().classes("w-full min-h-screen"):
        # ── Header ──
        with ui.row().classes(
            "w-full px-8 py-6 items-center justify-between shadow-lg text-white"
        ).style("background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);"):
            with ui.row().classes("items-center gap-4"):
                ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                    "flat round dense color=white"
                ).tooltip("Back to projects")
                ui.icon("contacts", size="40px").props("color=primary")
                ui.label("Clients").classes("text-2xl font-black tracking-tighter")

            ui.button(icon="dark_mode", on_click=lambda: dm.toggle()).props(
                "flat round dense color=white"
            ).tooltip("Toggle dark mode")

        with ui.column().classes("w-full max-w-4xl mx-auto px-6 py-8 gap-6"):
            # ── Add client button ──
            with ui.row().classes("w-full justify-between items-center"):
                ui.label("Client list").classes("text-lg font-bold")
                ui.button("Add client", icon="person_add", on_click=lambda: _open_edit_dialog(None)).props(
                    "unelevated color=positive rounded"
                ).classes("text-xs")

            _clients_container = ui.column().classes("w-full gap-3")

            def _refresh_clients():
                _clients_container.clear()
                clients = store.list_clients()
                with _clients_container:
                    if not clients:
                        ui.label("No clients added yet.").classes("opacity-50 py-8")
                        return
                    for c in clients:
                        with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                            with ui.row().classes("w-full items-center justify-between"):
                                with ui.column().classes("flex-1 gap-1"):
                                    ui.label(c.name).classes("text-base font-bold")
                                    with ui.row().classes("gap-2 text-[10px] opacity-60"):
                                        ui.label(f"{c.address}, {c.postal_code} {c.city}")
                                        if c.use_eracun:
                                            ui.badge("e-Račun", color="blue").classes("text-[8px]")
                                    if c.vat_id:
                                        ui.label(f"VAT ID: {ClientStore.mask_vat(c.vat_id)}").classes("text-[9px] opacity-40")
                                    if c.iban:
                                        ui.label(f"IBAN: {ClientStore.mask_iban(c.iban)}").classes("text-[9px] opacity-40")
                                    if c.responsible_persons:
                                        persons = ", ".join(p["name"] for p in c.responsible_persons)
                                        ui.label(f"Responsible: {persons}").classes("text-[9px] opacity-40")

                                with ui.row().classes("gap-1"):
                                    ui.button(
                                        icon="edit",
                                        on_click=lambda _, cid=c.id: _open_edit_dialog(cid),
                                    ).props("flat round dense size=sm color=primary").tooltip("Edit")
                                    ui.button(
                                        icon="delete",
                                        on_click=lambda _, cid=c.id: _confirm_delete_client(cid),
                                    ).props("flat round dense size=sm color=negative").tooltip("Delete")

            def _open_edit_dialog(cid: str | None):
                rec = store.get_client(cid) if cid else None
                with ui.dialog().props("persistent") as dlg:
                    with ui.card().classes("min-w-[620px] max-w-[760px] p-6 gap-4").style("max-height: 90vh"):
                        ui.label("Edit client" if rec else "New client").classes("text-lg font-bold")

                        _name = ui.input("Name", value=rec.name if rec else "").props("outlined dense").classes("w-full")
                        _address = ui.input("Address", value=rec.address if rec else "").props("outlined dense").classes("w-full")
                        with ui.row().classes("w-full gap-4"):
                            _postal = ui.input("Postal code", value=rec.postal_code if rec else "").props("outlined dense").classes("w-32")
                            _city = ui.input("City", value=rec.city if rec else "").props("outlined dense").classes("flex-1")
                        with ui.row().classes("w-full gap-4"):
                            _country = ui.input("Country", value=rec.country if rec else "Slovenija").props("outlined dense").classes("flex-1")
                            _cc = ui.input("Country code", value=rec.country_code if rec else "SI").props("outlined dense").classes("w-20")
                        with ui.row().classes("w-full gap-4 items-center"):
                            _vat_obl = ui.checkbox(
                                "VAT registered (ID za DDV — SI prefix on tax number)",
                                value=rec.vat_obliged if rec else False,
                            ).classes("flex-1 text-xs")
                            _vat = ui.input("Tax no. (davčna št.)", value=rec.vat_id if rec else "").props("outlined dense").classes("w-44")
                            _maticna = ui.input("Reg. no. (matična)", value=rec.maticna if rec else "").props("outlined dense").classes("w-44")
                        with ui.row().classes("w-full gap-4"):
                            _iban = ui.input("IBAN", value=rec.iban if rec else "").props("outlined dense").classes("flex-1")
                            _bic = ui.input("BIC/SWIFT", value=rec.bic if rec else "").props("outlined dense").classes("w-40")
                        with ui.row().classes("w-full gap-4"):
                            _bank = ui.input("Bank name", value=rec.bank_name if rec else "").props("outlined dense").classes("flex-1")
                            _holder = ui.input("Account holder", value=rec.account_holder if rec else "").props("outlined dense").classes("flex-1")
                        _eracun = ui.checkbox("e-Invoice (eSLOG) — IBAN required", value=rec.use_eracun if rec else False).classes("text-xs")

                        # ── Invoice defaults (pre-filled on invoices) ──
                        ui.label("Invoice defaults").classes("text-[10px] font-bold uppercase opacity-60 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            _d_order = ui.input("Order no.", value=rec.default_order_number if rec else "dogovor").props("outlined dense").classes("flex-1")
                            _d_proj = ui.input("Project code", value=rec.default_project_code if rec else "/").props("outlined dense").classes("w-32")
                        with ui.row().classes("w-full gap-4"):
                            _d_type = ui.select(
                                ["Pogodba", "Naročilnica", "Dnevni red", "Dogovor"],
                                value=rec.default_doc_type if rec else "Pogodba",
                            ).props("outlined dense").classes("flex-1")
                            _d_ref = ui.input("Reference doc no.", value=rec.default_doc_ref if rec else "").props("outlined dense").classes("flex-1")

                        # ── Rates (per service_type × target_lang × unit) ──
                        ui.label("Rates (EUR) — per service and direction").classes(
                            "text-[10px] font-bold uppercase opacity-60 mt-2"
                        )
                        _rate_inputs: dict[str, Any] = {}
                        import config as _cfg
                        _priced_services = ["Prevod", "Prevod urejanje", "Prevod urejanje korektur", "Lektura"]
                        _other_services = [s for s in _cfg.INVOICE_SERVICE_TYPES if s not in _priced_services]

                        with ui.scroll_area().classes("w-full h-48 border rounded p-2"):
                            with ui.column().classes("w-full gap-2"):
                                for svc in _priced_services:
                                    ui.label(svc).classes("text-[9px] font-bold uppercase opacity-70")
                                    with ui.row().classes("w-full gap-2 items-center flex-wrap"):
                                        for tgt in _cfg.INVOICE_TARGET_LANGS[:4]:
                                            with ui.column().classes("gap-0.5"):
                                                ui.label(f"→{tgt}").classes("text-[8px] opacity-50")
                                                for unit in ["stran", "ura", "pavšal", "znak", "avtorska pola"]:
                                                    _rk = _cfg.rate_key(svc, tgt, unit)
                                                    has = rec and _rk in rec.rates
                                                    _rv = float(rec.rates.get(_rk, 0)) if has else None
                                                    _inp = ui.number(
                                                        label=unit,
                                                        value=_rv,
                                                        min=0,
                                                        step=0.5,
                                                        format="%.2f",
                                                    ).props("outlined dense").classes("w-24 text-[9px]")
                                                    _rate_inputs[_rk] = _inp
                                                    if has:
                                                        _inp.style("border-bottom: 2px solid #22c55e")

                                ui.label("Other services").classes("text-[9px] font-bold uppercase opacity-70")
                                with ui.row().classes("w-full gap-2 flex-wrap"):
                                    for svc in _other_services:
                                        for unit in ["pavšal", "ura", "stran", "kos"]:
                                            _rk = f"{svc}:{unit}"
                                            _rv = float(rec.rates.get(_rk, 0)) if rec and _rk in rec.rates else None
                                            _inp = ui.number(
                                                label=f"{svc} {unit}",
                                                value=_rv,
                                                min=0,
                                                step=0.5,
                                                format="%.2f",
                                            ).props("outlined dense").classes("w-28 text-[9px]")
                                            _rate_inputs[_rk] = _inp

                        # ── Responsible persons ──
                        ui.label("Responsible persons").classes("text-[10px] font-bold uppercase opacity-60 mt-2")
                        _persons_container = ui.column().classes("w-full gap-1")
                        _persons: list[dict] = list(rec.responsible_persons) if rec else []

                        def _render_persons():
                            _persons_container.clear()
                            with _persons_container:
                                for i, p in enumerate(_persons):
                                    # Extract current string values from existing widgets
                                    if "_widgets" in p:
                                        _cur_name = p["name"].value or ""
                                        _cur_role = p["role"].value or ""
                                    else:
                                        _cur_name = p.get("name", "")
                                        _cur_role = p.get("role", "")
                                    _pn = ui.input(value=_cur_name).props("outlined dense").classes("flex-1 text-[10px]")
                                    _pr = ui.input(value=_cur_role).props("outlined dense").classes("w-32 text-[10px]")
                                    ui.button(icon="delete", on_click=lambda _, idx=i: _del_person(idx)).props(
                                        "flat round dense size=sm color=negative"
                                    )
                                    _persons[i] = {"name": _pn, "role": _pr, "_widgets": True}

                        def _add_person():
                            _persons.append({"name": "", "role": ""})
                            _render_persons()

                        def _del_person(idx: int):
                            if idx < len(_persons):
                                _persons.pop(idx)
                                _render_persons()

                        with ui.row().classes("w-full gap-2"):
                            ui.button("Add person", icon="person_add", on_click=_add_person).props(
                                "flat dense no-caps color=primary size=sm"
                            ).classes("text-[10px]")

                        if _persons:
                            _render_persons()

                        # ── Save ──
                        with ui.row().classes("w-full justify-end gap-2 mt-4"):
                            ui.button("Cancel", on_click=lambda: dlg.close()).props("flat color=grey")

                            def _save():
                                final_persons = []
                                for p in _persons:
                                    if "_widgets" in p:
                                        final_persons.append({
                                            "name": p["name"].value or "",
                                            "role": p["role"].value or "",
                                        })
                                    else:
                                        final_persons.append(p)
                                rates = {}
                                for rk, inp in _rate_inputs.items():
                                    v = inp.value
                                    if v:
                                        rates[rk] = str(v)

                                data = {
                                    "name": _name.value or "",
                                    "address": _address.value or "",
                                    "postal_code": _postal.value or "",
                                    "city": _city.value or "",
                                    "country": _country.value or "Slovenija",
                                    "country_code": _cc.value or "SI",
                                    "vat_id": _vat.value or "",
                                    "vat_obliged": _vat_obl.value,
                                    "iban": _iban.value or "",
                                    "bic": _bic.value or "",
                                    "bank_name": _bank.value or "",
                                    "account_holder": _holder.value or "",
                                    "maticna": _maticna.value or "",
                                    "default_order_number": _d_order.value or "dogovor",
                                    "default_project_code": _d_proj.value or "/",
                                    "default_doc_type": _d_type.value or "Pogodba",
                                    "default_doc_ref": _d_ref.value or "",
                                    "use_eracun": _eracun.value,
                                    "rates": rates,
                                    "responsible_persons": final_persons,
                                }

                                if not data["name"]:
                                    ui.notify("Name is required.", type="warning")
                                    return
                                if data["use_eracun"] and not data["iban"]:
                                    ui.notify("IBAN is required for e-invoice clients.", type="warning")
                                    return

                                if rec:
                                    store.update_client(rec.id, data)
                                    ui.notify("Client updated.", type="positive")
                                else:
                                    store.add_client(data)
                                    ui.notify("Client added.", type="positive")

                                dlg.close()
                                _refresh_clients()

                            ui.button("Save", icon="save", on_click=_save).props(
                                "unelevated color=positive"
                            )

                dlg.open()

            def _confirm_delete_client(cid: str):
                rec = store.get_client(cid)
                if not rec:
                    return
                with ui.dialog() as dlg:
                    with ui.card().classes("min-w-[420px]"):
                        ui.label("Delete client?").classes("text-lg font-bold mb-2")
                        ui.label(f"Client \"{rec.name}\" will be permanently deleted.").classes("text-sm opacity-70 mb-4")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Cancel", on_click=lambda: dlg.close()).props("flat")
                            ui.button(
                                "Delete",
                                on_click=lambda: (dlg.close(), store.delete_client(cid), ui.notify("Client deleted", type="warning"), _refresh_clients()),
                            ).props("color=negative unelevated")
                    dlg.open()

            _refresh_clients()


# ===========================================================================
# PAGE /invoices — Invoice overview & archive
# ===========================================================================
@ui.page("/invoices")
def page_invoices():
    from translate_core.client_store import ClientStore
    from datetime import date
    from decimal import Decimal

    apply_colors()
    from ui import settings as ui_settings
    dm = ui_settings.install_dark_mode()
    ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")

    store = ClientStore()
    out_dir = config.INVOICE_OUTPUT_DIR
    current_year = date.today().year

    with ui.column().classes("w-full min-h-screen"):
        with ui.row().classes(
            "w-full px-8 py-6 items-center justify-between shadow-lg text-white"
        ).style("background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);"):
            with ui.row().classes("items-center gap-4"):
                ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                    "flat round dense color=white"
                ).tooltip("Back to projects")
                ui.icon("receipt_long", size="40px").props("color=primary")
                ui.label("Invoices").classes("text-2xl font-black tracking-tighter")
            with ui.row().classes("gap-3 items-center"):
                ui.button(icon="contacts", on_click=lambda: ui.navigate.to("/clients")).props(
                    "flat round dense color=white"
                ).tooltip("Clients")
                ui.button(icon="dark_mode", on_click=lambda: dm.toggle()).props(
                    "flat round dense color=white"
                ).tooltip("Toggle dark mode")

        with ui.column().classes("w-full max-w-5xl mx-auto px-6 py-8 gap-6"):
            # ── Invoice counter + year report ──
            with ui.row().classes("w-full justify-between items-center"):
                ui.label("Invoice archive").classes("text-lg font-bold")
                ui.label(f"Archive folder: {out_dir}").classes(
                    "text-[9px] font-mono opacity-40"
                )

            with ui.row().classes("w-full gap-4 items-end"):
                with ui.column().classes("gap-1"):
                    ui.label("Invoice counter").classes("text-[10px] font-bold uppercase opacity-60")
                    with ui.row().classes("gap-2 items-center"):
                        _year_sel = ui.number(
                            label="Year", value=current_year, min=2020, max=2099,
                        ).props("outlined dense").classes("w-24")
                        _counter_val = ui.label("").classes("text-sm font-bold")
                        _set_start = ui.number(
                            label="Start from #", value=None, min=1,
                        ).props("outlined dense").classes("w-32")
                        ui.button("Set", on_click=lambda: _do_set_counter()).props(
                            "flat dense no-caps color=primary size=sm"
                        ).classes("text-[10px]")

                with ui.column().classes("gap-1"):
                    ui.label("Yearly report").classes("text-[10px] font-bold uppercase opacity-60")
                    with ui.row().classes("gap-2"):
                        _report_year = ui.number(
                            label="Year", value=current_year, min=2020, max=2099,
                        ).props("outlined dense").classes("w-24")
                        ui.button("Generate report", on_click=lambda: _gen_report()).props(
                            "flat dense no-caps color=primary size=sm"
                        ).classes("text-[10px]")

            def _update_counter_label():
                yr = int(_year_sel.value or current_year)
                cur = store.get_invoice_counter(yr)
                _counter_val.set_text(f"Next: {yr}-{cur + 1:03d}")

            def _do_set_counter():
                yr = int(_year_sel.value or current_year)
                start = int(_set_start.value or 1)
                store.set_invoice_counter(yr, start)
                ui.notify(f"Counter set: next invoice will be {yr}-{start:03d}", type="positive")
                _update_counter_label()

            def _gen_report():
                from translate_core.billing_report import generate_pdf, generate_xlsx
                yr = int(_report_year.value or current_year)
                invoices = store.list_invoices(yr)
                if not invoices:
                    ui.notify(f"No invoices found for {yr}.", type="warning")
                    return
                report_projects = []
                for inv in invoices:
                    for li in inv.get("line_items") or []:
                        report_projects.append({
                            "filename": li.get("desc", ""),
                            "lang_pair": li.get("lang_pair", "").replace(">", "->").lower() if ">" in li.get("lang_pair","") else "en->sl",
                            "chars_with": 0,
                            "chars_without": 0,
                            "pages": float(li.get("qty", "0")),
                            "rate": float(li.get("price", "0")),
                            "total": float(Decimal(li.get("qty", "0")) * Decimal(li.get("price", "0"))),
                            "responsible_person": li.get("responsible_person", ""),
                        })
                ts = f"{yr}"
                pdf = generate_pdf(report_projects)
                xlsx = generate_xlsx(report_projects)
                ui.download(xlsx, filename=f"invoice_report_{yr}.xlsx")
                ui.download(pdf, filename=f"invoice_report_{yr}.pdf")
                ui.notify(f"Yearly report for {yr}: {len(invoices)} invoices, {len(report_projects)} line items", type="positive")

            _inv_container = ui.column().classes("w-full gap-3")
            _update_counter_label()
            _year_sel.on_value_change(lambda e: _update_counter_label())

            def _regenerate_row(inv: dict):
                """Rebuild PDF (+XML) from stored record into the archive folder."""
                from translate_core.invoice_models import InvoiceData, InvoiceLineItem
                from translate_core.invoice_eslog import generate_eslog_xml, generate_eslog_pdf

                rec = store.get_client(inv["client_id"])
                if not rec:
                    ui.notify("Client for this invoice no longer exists.", type="warning")
                    return
                items = [
                    InvoiceLineItem(
                        description=li.get("desc", ""),
                        service_type=li.get("service_type", "Prevod"),
                        lang_pair=li.get("lang_pair", "ENG>SLO"),
                        unit=li.get("unit", "stran"),
                        quantity=Decimal(li.get("qty", "0")),
                        unit_price=Decimal(li.get("price", "0")),
                        responsible_person=li.get("responsible_person", ""),
                    )
                    for li in inv.get("line_items") or []
                ]
                data = InvoiceData(
                    invoice_number=inv["invoice_number"],
                    issue_date=date.fromisoformat(inv["issue_date"]),
                    due_date=date.fromisoformat(inv["due_date"]),
                    service_date_from=date.fromisoformat(inv["service_date_from"] or inv["issue_date"]),
                    service_date_to=date.fromisoformat(inv["service_date_to"] or inv["issue_date"]),
                    client=rec,
                    issuer=config.ISSUER,
                    line_items=items,
                    approver=inv.get("approver") or "",
                    order_number=inv.get("order_number") or "dogovor",
                    project_code=inv.get("project_code") or "/",
                    legal_notes=config.INVOICE_LEGAL_NOTES,
                )
                num = inv["invoice_number"]
                out_dir.mkdir(parents=True, exist_ok=True)
                if inv["invoice_type"] == "eracun":
                    (out_dir / f"Racun_{num}.xml").write_bytes(generate_eslog_xml(data))
                from translate_core.invoice_plain import generate_plain_invoice_xlsx
                xlsx = generate_plain_invoice_xlsx(data)
                (out_dir / f"Racun_{num}.xlsx").write_bytes(xlsx)
                (out_dir / f"Racun_{num}.pdf").write_bytes(generate_eslog_pdf(data))
                ui.notify(f"Regenerated into archive: Racun_{num}", type="positive")
                _refresh()

            def _delete_row(inv: dict):
                """Delete an invoice record + optionally its archive files."""
                with ui.dialog() as dlg:
                    with ui.card().classes("min-w-[420px]"):
                        ui.label("Delete invoice?").classes("text-lg font-bold mb-2")
                        ui.label(
                            f"Invoice {inv['invoice_number']} will be permanently deleted. "
                            "Archive files on disk will also be removed."
                        ).classes("text-sm opacity-70 mb-4")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Cancel", on_click=lambda: dlg.close()).props("flat")
                            ui.button("Delete permanently", on_click=lambda: None).props(
                                "color=negative unelevated"
                            ).on("click", lambda: None)

                            def _do_delete():
                                store.delete_invoice(inv["invoice_number"])
                                for ext in (".pdf", ".xml", ".xlsx"):
                                    p = out_dir / f"Racun_{inv['invoice_number']}{ext}"
                                    if p.exists():
                                        p.unlink()
                                dlg.close()
                                ui.notify("Invoice deleted", type="warning")
                                _refresh()

                            ui.button("Delete permanently", icon="delete", on_click=_do_delete).props(
                                "color=negative unelevated"
                            )
                dlg.open()

            def _edit_invoice(inv: dict):
                """Open an edit dialog for an existing invoice — modify line items,
                dates, approver, then save & regenerate."""
                from decimal import Decimal
                from datetime import date as _date
                import config as cfg
                from translate_core.invoice_models import InvoiceData, InvoiceLineItem
                from translate_core.invoice_eslog import (
                    generate_eslog_xml, generate_eslog_pdf, validate_eslog_xml,
                )
                from translate_core.invoice_plain import (
                    generate_plain_invoice_xlsx, generate_plain_invoice_pdf,
                )

                rec = store.get_client(inv["client_id"])
                if not rec:
                    ui.notify("Client no longer exists.", type="warning")
                    return

                stored_items = inv.get("line_items") or []

                with ui.dialog().props("persistent") as _edlg:
                    with ui.card().classes("min-w-[720px] max-w-[900px] p-6 gap-4"):
                        ui.label(f"Edit invoice {inv['invoice_number']}").classes("text-lg font-bold")
                        ui.label(f"Client: {rec.name}").classes("text-xs opacity-50")

                        # ── Date editors ──
                        with ui.row().classes("w-full gap-2 flex-wrap"):
                            _e_issue = ui.date(label="Issue date",
                                value=_date.fromisoformat(inv["issue_date"][:10]).isoformat())
                            _e_due = ui.date(label="Due date",
                                value=_date.fromisoformat(inv["due_date"][:10]).isoformat())
                            _e_svc_f = ui.date(label="Service from",
                                value=_date.fromisoformat((inv.get("service_date_from") or inv["issue_date"])[:10]).isoformat())
                            _e_svc_t = ui.date(label="Service to",
                                value=_date.fromisoformat((inv.get("service_date_to") or inv["issue_date"])[:10]).isoformat())

                        # ── Order info ──
                        with ui.row().classes("w-full gap-2"):
                            _e_order = ui.input("Order no.", value=inv.get("order_number") or "dogovor").props("outlined dense").classes("flex-1 text-[10px]")
                            _e_proj = ui.input("Project code", value=inv.get("project_code") or "/").props("outlined dense").classes("flex-1 text-[10px]")
                            persons = {p["name"]: p["name"] for p in rec.responsible_persons}
                            _e_appr = ui.select(persons or {}, value=inv.get("approver") or None,
                                label="Approver").props("outlined dense").classes("flex-1 text-[10px]")

                        # ── Line items editor ──
                        ui.label("Line items").classes("text-xs font-bold uppercase opacity-60 mt-2")
                        _e_lines: list[dict] = []
                        _e_container = ui.column().classes("w-full gap-1")

                        def _e_upd_line(lw):
                            q = float(lw["qty"].value or 0)
                            pr = float(lw["price"].value or 0)
                            lw["total_lbl"].set_text(f"€{q * pr:,.2f}")
                            _e_grand.set_text(f"€{sum(float(l['qty'].value or 0) * float(l['price'].value or 0) for l in _e_lines):,.2f}")

                        def _e_make_row(li: dict | None = None):
                            with _e_container:
                                with ui.row().classes("w-full gap-1 items-center") as row_el:
                                    _s = ui.select(cfg.INVOICE_SERVICE_TYPES,
                                        value=(li or {}).get("service_type", "Prevod")).props("outlined dense").classes("flex-[2] text-[10px]")
                                    _p = ui.select(cfg.INVOICE_LANG_PAIRS,
                                        value=(li or {}).get("lang_pair", "ENG>SLO")).props("outlined dense").classes("flex-[2] text-[10px]")
                                    _u = ui.select(cfg.INVOICE_UNITS,
                                        value=(li or {}).get("unit", "stran")).props("outlined dense").classes("w-28 text-[10px]")
                                    _q = ui.number(value=float(Decimal(str((li or {}).get("qty", "1")))),
                                        min=0, step=0.01, format="%.2f").props("outlined dense").classes("w-20 text-[10px]")
                                    _pr = ui.number(value=float(Decimal(str((li or {}).get("price", "0")))),
                                        min=0, step=0.01, format="%.4f").props("outlined dense").classes("w-20 text-[10px]")
                                    _d = ui.input(value=(li or {}).get("desc", "")).props("outlined dense").classes("flex-[3] text-[10px]")
                                    _rm = ui.button(icon="delete").props("flat round dense size=sm color=negative")
                                    _tl = ui.label("€0.00").classes("text-[10px] font-bold tabular-nums text-positive w-16 text-right")
                                    lw = {"svc": _s, "pair": _p, "unit": _u, "qty": _q,
                                          "price": _pr, "desc": _d, "total_lbl": _tl,
                                          "responsible_person": (li or {}).get("responsible_person", ""),
                                          "__rem": _rm, "__row": row_el}
                                    _q.on_value_change(lambda e, l=lw: _e_upd_line(l))
                                    _pr.on_value_change(lambda e, l=lw: _e_upd_line(l))
                                    def _del(l=lw):
                                        if l in _e_lines:
                                            _e_lines.remove(l)
                                        try: l["__row"].delete()
                                        except: pass
                                        _e_upd_line(l)
                                    _rm.on_click(lambda: _del())
                                    _e_upd_line(lw)
                            return lw

                        for li in stored_items:
                            _e_lines.append(_e_make_row(li))

                        def _e_add_row():
                            lw = _e_make_row(None)
                            _e_lines.append(lw)

                        with ui.row().classes("w-full justify-between items-center"):
                            ui.button("Add line item", icon="add", on_click=_e_add_row).props(
                                "flat dense no-caps color=primary size=sm").classes("text-[10px]")
                            with ui.row().classes("items-baseline gap-2"):
                                ui.label("Total").classes("text-[10px] uppercase opacity-50")
                                _e_grand = ui.label("€0.00").classes("text-base font-black tabular-nums text-positive")

                        # Initial total
                        _e_upd_line(_e_lines[0]) if _e_lines else None

                        # ── Save & Regenerate ──
                        with ui.row().classes("w-full justify-end gap-2 mt-4"):
                            ui.button("Cancel", on_click=lambda: _edlg.close()).props("flat color=grey")

                            def _e_save():
                                items = []
                                for lw in _e_lines:
                                    q = Decimal(str(lw["qty"].value or 0))
                                    pr = Decimal(str(lw["price"].value or 0))
                                    if q != 0 or pr != 0:
                                        items.append(InvoiceLineItem(
                                            description=lw["desc"].value or "",
                                            service_type=lw["svc"].value or "Prevod",
                                            lang_pair=lw["pair"].value or "ENG>SLO",
                                            unit=lw["unit"].value or "stran",
                                            quantity=q,
                                            unit_price=pr,
                                            responsible_person=lw.get("responsible_person", ""),
                                        ))
                                if not items:
                                    ui.notify("No valid line items.", type="warning")
                                    return

                                issue_d = _date.fromisoformat(_e_issue.value)
                                due_d = _date.fromisoformat(_e_due.value)
                                svc_f = _date.fromisoformat(_e_svc_f.value)
                                svc_t = _date.fromisoformat(_e_svc_t.value)
                                approver = _e_appr.value or ""
                                num = inv["invoice_number"]

                                inv_data = InvoiceData(
                                    invoice_number=num,
                                    issue_date=issue_d,
                                    due_date=due_d,
                                    service_date_from=svc_f,
                                    service_date_to=svc_t,
                                    client=rec,
                                    issuer=config.ISSUER,
                                    line_items=items,
                                    approver=approver,
                                    order_number=_e_order.value or "dogovor",
                                    project_code=_e_proj.value or "/",
                                    legal_notes=config.INVOICE_LEGAL_NOTES,
                                )

                                # Regenerate files
                                out_dir.mkdir(parents=True, exist_ok=True)
                                xlsx_path = xml_path = pdf_path = None
                                if inv["invoice_type"] == "eracun":
                                    xml_bytes = generate_eslog_xml(inv_data)
                                    ok, err = validate_eslog_xml(xml_bytes)
                                    if not ok:
                                        ui.notify(f"eSLOG validation failed: {err[:300]}", type="negative")
                                        return
                                    xml_path = str(out_dir / f"Racun_{num}.xml")
                                    (out_dir / f"Racun_{num}.xml").write_bytes(xml_bytes)
                                    pdf_path = str(out_dir / f"Racun_{num}.pdf")
                                    (out_dir / f"Racun_{num}.pdf").write_bytes(generate_eslog_pdf(inv_data))
                                else:
                                    xlsx = generate_plain_invoice_xlsx(inv_data)
                                    xlsx_path = str(out_dir / f"Racun_{num}.xlsx")
                                    (out_dir / f"Racun_{num}.xlsx").write_bytes(xlsx)
                                    try:
                                        pdf_path = str(out_dir / f"Racun_{num}.pdf")
                                        (out_dir / f"Racun_{num}.pdf").write_bytes(
                                            generate_plain_invoice_pdf(xlsx))
                                    except RuntimeError:
                                        pdf_path = None

                                # Update stored record
                                store.delete_invoice(num)
                                store.record_invoice({
                                    "invoice_number": num,
                                    "issue_date": issue_d.isoformat(),
                                    "due_date": due_d.isoformat(),
                                    "service_date_from": svc_f.isoformat(),
                                    "service_date_to": svc_t.isoformat(),
                                    "invoice_type": inv["invoice_type"],
                                    "client_id": inv["client_id"],
                                    "responsible_person": approver,
                                    "approver": approver,
                                    "order_number": _e_order.value or "",
                                    "project_code": _e_proj.value or "",
                                    "doc_type": inv.get("doc_type", "Pogodba"),
                                    "doc_date": inv.get("doc_date", ""),
                                    "total": str(inv_data.total),
                                    "pdf_path": pdf_path,
                                    "xml_path": xml_path,
                                    "xlsx_path": xlsx_path,
                                    "line_items": [
                                        {"desc": li.description, "qty": str(li.quantity),
                                         "price": str(li.unit_price), "unit": li.unit,
                                         "service_type": li.service_type,
                                         "lang_pair": li.lang_pair,
                                         "responsible_person": li.responsible_person}
                                        for li in items
                                    ],
                                })

                                _edlg.close()
                                ui.notify(f"Invoice {num} updated and regenerated", type="positive")
                                _refresh()

                            ui.button("Save & Regenerate", icon="save", on_click=_e_save).props(
                                "unelevated color=positive")
                _edlg.open()
            def _refresh():
                _inv_container.clear()
                invoices = store.list_invoices()
                with _inv_container:
                    if not invoices:
                        ui.label("No invoices issued yet.").classes("opacity-50 py-8")
                        return
                    for inv in invoices:
                        client = store.get_client(inv["client_id"])
                        cname = client.name if client else "(deleted client)"
                        type_label = "e-Račun" if inv["invoice_type"] == "eracun" else "Račun"
                        with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                            with ui.row().classes("w-full items-center justify-between gap-4"):
                                with ui.column().classes("flex-1 gap-1 min-w-0"):
                                    with ui.row().classes("gap-2 items-baseline"):
                                        ui.label(inv["invoice_number"]).classes("text-base font-bold")
                                        ui.badge(type_label, color=(
                                            "blue" if inv["invoice_type"] == "eracun" else "teal"
                                        )).classes("text-[8px]")
                                    ui.label(cname).classes("text-[11px] font-medium")
                                    ui.label(
                                        f"Issued {_sl_date_str(inv['issue_date'])}  ·  "
                                        f"Due {_sl_date_str(inv['due_date'])}  ·  "
                                        f"Approved by: {inv.get('approver') or '—'}"
                                    ).classes("text-[9px] opacity-50")
                                with ui.row().classes("items-center gap-3 shrink-0"):
                                    ui.label(f"{inv['total']} EUR").classes(
                                        "text-base font-black tabular-nums text-positive"
                                    )
                                    files_exist = bool(
                                        inv.get("pdf_path") and Path(inv["pdf_path"]).exists()
                                    )
                                    ui.icon("check_circle" if files_exist else "file_present",
                                            size="14px").props(
                                        "color=positive" if files_exist else "color=grey-5"
                                    ).tooltip(
                                        "PDF in archive" if files_exist else "Not yet archived"
                                    )
                                    ui.button(
                                        icon="download",
                                        on_click=lambda _, i=inv: _download_row(i),
                                    ).props("flat round dense size=sm color=primary").tooltip("Download files")
                                    ui.button(
                                        icon="edit",
                                        on_click=lambda _, i=inv: _edit_invoice(i),
                                    ).props("flat round dense size=sm color=primary").tooltip("Edit invoice")
                                    ui.button(
                                        icon="replay",
                                        on_click=lambda _, i=inv: _regenerate_row(i),
                                    ).props("flat round dense size=sm color=primary").tooltip("Regenerate into archive")
                                    ui.button(
                                        icon="delete",
                                        on_click=lambda _, i=inv: _delete_row(i),
                                    ).props("flat round dense size=sm color=negative").tooltip("Delete invoice")

            def _sl_date_str(iso: str) -> str:
                try:
                    return date.fromisoformat(iso[:10]).strftime("%d.%m.%Y")
                except Exception:
                    return iso

            def _download_file(path: str | None, name: str):
                if path and Path(path).exists():
                    ui.download(Path(path).read_bytes(), filename=name)
                else:
                    ui.notify("File not in archive yet — use Regenerate.", type="warning")

            def _download_row(inv: dict):
                num = inv["invoice_number"]
                if inv.get("xml_path"):
                    _download_file(inv["xml_path"], f"Racun_{num}.xml")
                if inv.get("pdf_path"):
                    _download_file(inv["pdf_path"], f"Racun_{num}.pdf")
                if inv.get("xlsx_path"):
                    _download_file(inv["xlsx_path"], f"Racun_{num}.xlsx")

            _refresh()

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
        # Default to 0.0.0.0 so the system service binding works.
        # Set APP_HOST=127.0.0.1 to restrict to localhost only.
        host=os.environ.get("APP_HOST", "0.0.0.0"),
        port=8080,
        show=True,
        storage_secret=config.STORAGE_SECRET,
        # kg.save on the 97MB KG can legitimately block a worker thread
        # for 1-3 s during a confirm. NiceGUI's default ping_timeout =
        # max(reconnect_timeout * 0.4, 2) = 2 s, which drops the page on
        # any such call. Widen the window so the editor survives it.
        reconnect_timeout=30.0,
    )
