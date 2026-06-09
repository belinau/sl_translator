# main.py
#
# Zen Translator — Main Application Entrypoint
# Fully integrated with advanced document pre-processing and compiled DOCX exports
#

import asyncio
import concurrent.futures
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

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
        Translator,
    )
except ImportError as e:
    print(f"\n[ERROR] Import failed: {e.name}")
    sys.exit(1)

# Global resources
# ---------------------------------------------------------------------------
tm: "TranslationMemory | None" = None
glossary: "Glossary | None" = None
kg: "KnowledgeGraph | None" = None
translator: "Translator | None" = None
doc_parser: "DocumentParser | None" = None
qa_engine: "QAEngine | None" = None
llm_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
GLOBAL_VOCAB: Dict[str, set] = {}  # Project ID -> Set of words

# ---------------------------------------------------------------------------
# Project persistence
# ---------------------------------------------------------------------------
PROJECTS_DIR = config.BASE_DIR / "data" / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Publish module-level paths + executor to the shared app_state immediately.
# Functions are wired further down once defined.
app_state.PROJECTS_DIR = PROJECTS_DIR
app_state.llm_executor = llm_executor
app_state.config = config


def validate_project_id(project_id: str) -> None:
    """Raise ValueError if project_id contains path traversal or unexpected chars."""
    if not re.fullmatch(r"[a-f0-9\-]{6,36}", project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")


def parse_lang_pair(pair: str) -> Tuple[str, str]:
    """Return (src, tgt) from 'src->tgt'. Defaults to ('en', 'sl')."""
    if not pair or "->" not in pair:
        return "en", "sl"
    src, _, tgt = pair.partition("->")
    return (src or "en"), (tgt or "sl")


def list_projects() -> list:
    projects = []
    for p in PROJECTS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            projects.append(
                {
                    "id": data["id"],
                    "filename": data["filename"],
                    "lang_pair": data.get("lang_pair", "en->sl"),
                    "saved_at": data["saved_at"],
                    "total": data["total"],
                    "done": data["done"],
                }
            )
        except Exception:
            pass
    projects.sort(key=lambda x: x["saved_at"], reverse=True)
    return projects


def save_project(ws: dict):
    validate_project_id(ws["project_id"])
    segs = [
        {
            "id": s["id"],
            "source": s["source"],
            "target": s["target"],
            "status": s["status"],
        }
        for s in ws["segments"]
    ]
    done = sum(1 for s in segs if s["status"] == "done")
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
    path = PROJECTS_DIR / f"{ws['project_id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_project(project_id: str):
    validate_project_id(project_id)
    path = PROJECTS_DIR / f"{project_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_project(project_id: str):
    validate_project_id(project_id)
    for suffix in [".json", ".docx", ".pdf", ".txt"]:
        p = PROJECTS_DIR / f"{project_id}{suffix}"
        if p.exists():
            p.unlink()


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
        for entry in tm.entries:
            if entry.get("source") == src_n and entry.get("origin") == "working.tmx":
                entry["target"] = tgt_n
                break
        else:
            tm.entries.append(
                {
                    "source": src_n,
                    "target": tgt_n,
                    "origin": "working.tmx",
                    "source_lang": src_lang,
                    "target_lang": tgt_lang,
                }
            )


async def init_resources():
    """Construct backend singletons and expose them via app_state so the UI
    layer reads live values regardless of which Python process owns this
    module (matters with NiceGUI's auto-reload: the worker runs as
    __mp_main__, not __main__)."""
    global tm, glossary, kg, doc_parser, translator, qa_engine
    try:
        tm = TranslationMemory()
        app_state.tm = tm
        glossary = Glossary()
        app_state.glossary = glossary
        kg = KnowledgeGraph()
        app_state.kg = kg
        doc_parser = DocumentParser()
        app_state.doc_parser = doc_parser
        translator = Translator()
        app_state.translator = translator
        qa_engine = QAEngine()
        qa_engine.build_lemma_index(glossary.entries)
        app_state.qa_engine = qa_engine
    except Exception as e:
        # Make startup failures loud so the UI doesn't silently see None
        # resources later.
        import traceback
        print(f"\n[FATAL] init_resources failed: {e}")
        traceback.print_exc()
        return

    # The MLX model is now lazy-loaded on first use. If the user disables
    # AI via the master toggle, the model never enters memory at all.


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
                    ui.label("Zen Translator").classes(
                        "text-3xl font-black tracking-tighter"
                    )
                    ui.label("Professional Translation Workspace").classes(
                        "text-xs font-bold uppercase tracking-[0.2em] opacity-60"
                    )

            with ui.row().classes("gap-3 items-center"):
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

                with ui.column().classes("w-full gap-3 mt-2"):
                    ai_master_switch = ui.switch(
                        "AI Translation",
                        value=ui_settings.ai_master_enabled(),
                        on_change=lambda e: ui_settings.set_ai_master_enabled(bool(e.value)),
                    ).classes("text-[11px]").tooltip(
                        "Master switch for AI/LLM translation. "
                        "When off, the language model is not loaded into memory and all AI "
                        "translation controls are disabled in the editor. "
                        "Disable for language pairs where you prefer manual translation only."
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

        ui.label("RECENT PROJECTS").classes(
            "text-[10px] font-black uppercase tracking-[0.3em] px-2 mb-2 opacity-60"
        )
        with ui.grid(columns=2).classes("w-full gap-4"):
            for p in projects:
                pct = int(p["done"] / p["total"] * 100) if p["total"] else 0
                with (
                    ui.card()
                    .props("flat bordered")
                    .classes("p-6 rounded-2xl flex flex-col gap-4 cursor-pointer hover:shadow-lg transition")
                    .on(
                        "click", lambda pid=p["id"]: ui.navigate.to(f"/translate/{pid}")
                    )
                ):
                    with ui.row().classes("w-full justify-between items-start"):
                        with ui.column().classes("gap-0.5 flex-1"):
                            ui.label(p["filename"]).classes(
                                "text-base font-bold truncate leading-tight"
                            )
                            ui.label(p["lang_pair"]).classes(
                                "text-[10px] font-black uppercase tracking-widest text-primary"
                            )
                        ui.button(
                            icon="delete",
                            on_click=lambda e, pid=p["id"], c=container: (
                                delete_and_refresh(pid, c, client)
                            ),
                        ).props("flat round dense size=sm color=grey-5").on("click.stop")

                    with ui.row().classes("w-full items-center gap-4 mt-2"):
                        with ui.column().classes("flex-1 gap-1"):
                            ui.linear_progress(value=pct / 100, color="positive").props(
                                "size=8px rounded"
                            ).classes("w-full")
                            with ui.row().classes("w-full justify-between items-center"):
                                ui.label(
                                    f"{p['done']} / {p['total']} segments"
                                ).classes("text-[10px] font-medium opacity-60")
                                ui.label(f"{pct}%").classes("text-[10px] font-bold")

                    ui.separator()
                    with ui.row().classes("w-full pt-1 items-center justify-between"):
                        ui.label(
                            f"Saved {p['saved_at'][:16].replace('T', ' ')}"
                        ).classes("text-[9px] font-medium italic opacity-50")
                        ui.icon("arrow_forward", size="14px").props("color=primary")


def delete_and_refresh(project_id: str, container: ui.column, client):
    ui.notify("Deleted", type="warning", timeout=1200)
    delete_project(project_id)
    render_project_list(container, client)


async def handle_new_upload(e, lang_pair: str):
    name = getattr(e, "name", "document.docx")
    suffix = Path(name).suffix.lower()
    if suffix not in (".docx", ".pdf"):
        return ui.notify("DOCX or PDF files only", type="warning")

    try:
        content = await e.file.read()
    except Exception as ex:
        return ui.notify(f"Error: {ex}", type="negative")

    project_id = str(uuid.uuid4())[:8]
    saved_path = PROJECTS_DIR / f"{project_id}{suffix}"
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    saved_path.write_bytes(content)

    # ── DOCX: direct python-docx paragraph extraction ────────────────────
    if suffix == ".docx":
        try:
            import io
            import docx as _docx
            doc = _docx.Document(io.BytesIO(content))
        except Exception as ex:
            return ui.notify(f"DOCX parse error: {ex}", type="negative")

        segments = []
        for p in doc.paragraphs:
            txt = p.text.strip()
            if txt:
                segments.append(
                    {"id": len(segments), "source": txt, "target": "", "status": "pending"}
                )

    # ── PDF: MarkItDown fallback ──────────────────────────────────────────
    else:
        if doc_parser is None:
            return ui.notify("Document parser not initialized", type="negative")
        try:
            md_text, _ = doc_parser.to_markdown_with_meta(
                saved_path, preprocess=True
            )
        except Exception as ex:
            return ui.notify(f"PDF parsing error: {ex}", type="negative")

        segments = []
        for block in md_text.split("\n\n"):
            txt = block.strip()
            if txt:
                segments.append(
                    {"id": len(segments), "source": txt, "target": "", "status": "pending"}
                )

    if not segments:
        return ui.notify("No text extracted from document", type="warning")

    ws = {
        "project_id": project_id,
        "filename": name,
        "lang_pair": lang_pair,
        "active_index": 0,
        "segments": segments,
    }

    save_project(ws)
    ui.notify(f"Created: {len(segments)} segments", type="positive")
    ui.navigate.to(f"/translate/{project_id}")


# Register the translation workspace page. Importing ui.workspace is a
# side-effect import: it runs the @ui.page("/translate/{project_id}") decorator.
# The alias avoids shadowing NiceGUI's `ui` and the explicit noqa silences
# both the import-position and unused-name checks.
from ui import workspace as _zen_workspace  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
_ = _zen_workspace  # mark the binding as deliberately consumed


# Publish module-level functions to app_state once they're all defined.
# These do NOT depend on init_resources running first.
app_state.save_project = save_project
app_state.load_project = load_project
app_state.save_pair_to_tm = save_pair_to_tm
app_state.parse_lang_pair = parse_lang_pair


def apply_colors():
    ui.colors(
        primary="#0f172a",
        secondary="#334155",
        positive="#10b981",
        accent="#3b82f6",
        negative="#ef4444",
    )


# Wire apply_colors as well, now that it's defined.
app_state.apply_colors = apply_colors


if __name__ in {"__main__", "__mp_main__"}:
    # storage_secret is required for app.storage.user (dark mode + AI pretranslate
    # toggle persistence). Any non-empty string works for a single-user desktop app.
    ui.run(
        title="Zen Translator",
        favicon="✨",
        port=8080,
        show=True,
        storage_secret="zen-translator-local-storage",
    )
