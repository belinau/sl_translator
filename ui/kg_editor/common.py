"""Shared KG editor chrome — sidebar with search, cached stats, navigation.

Provides kg_frame(): top bar + left drawer with search input, type chips,
cached stats, hard save button. Used by the browser page and both review
pages. Also provides render_record() and build_reclass_inputs() used by
the review pages.
"""
from __future__ import annotations

from nicegui import run, ui

from translate_core.kg_review_ops import (
    AGENT_ROLES,
    INSTITUTION_KINDS,
    RECLASS_AGENT_ROLES,
    RECLASS_PROJECT_TYPES,
    RECLASS_TARGETS,
)


# -----------------------------------------------------------------------
# Navigation — only 3 pages now
# -----------------------------------------------------------------------
_PAGES = [
    ("/kg", "Browser", "search"),
    ("/kg/review", "Extraction Review", "inbox"),
    ("/kg/kg-review", "KG Review", "rule"),
]


# -----------------------------------------------------------------------
# Ontology constants for the edit panel
# -----------------------------------------------------------------------
NODE_TYPES = ["concept", "agent", "source_text", "term", "institution", "translation_mapping"]

_CONCEPT_RELATIONS = ["extends", "critiques", "redefines", "reappropriates", "related_to"]

PROJECT_TYPES = [
    "book_translation", "article_translation", "festival_programme",
    "exhibition_catalogue", "book", "book_chapter", "journal_article",
    "magazine_article", "newspaper_article", "web_source",
    "exhibition_catalog", "interview", "thesis_dissertation",
    "artwork", "performance", "cited_container", "cited_work",
]

# Valid edge relations per (source_type, target_type) — from ontology §3
EDGE_RELATIONS: dict[tuple[str, str], list[str]] = {
    ("term", "translation_mapping"): ["has_mapping"],
    ("translation_mapping", "term"): ["maps_to"],
    ("term", "term"): ["translates_to"],
    ("term", "concept"): ["instantiates_concept"],
    ("source_text", "agent"): ["written_by", "translated_by", "edited_by", "performed_by"],
    ("source_text", "institution"): ["published_by", "translation_published_by", "hosted_by"],
    ("source_text", "source_text"): ["cited_in", "appears_in"],
    ("translation_mapping", "source_text"): ["instantiated_in"],
    ("translation_mapping", "agent"): ["attributed_to"],
    ("concept", "agent"): ["attributed_to"],
    ("concept", "concept"): _CONCEPT_RELATIONS,
}


# -----------------------------------------------------------------------
# Resource access with still-initializing guard
# -----------------------------------------------------------------------

def get_resources() -> tuple:
    """Return ``(kg, glossary)`` from ``app_state``."""
    import app_state
    kg = app_state.kg
    glossary = app_state.glossary
    if kg is None:
        return None, None
    return kg, glossary


# -----------------------------------------------------------------------
# Shared chrome: top bar + left drawer
# -----------------------------------------------------------------------

def kg_frame(active: str):
    """Build the shared KG editor frame and return ``(kg, glossary)``.

    *active* is the path of the current page (e.g. ``"/kg"``) used to
    highlight the active nav button.

    Returns ``(None, None)`` if resources are still initializing.
    """
    kg, glossary = get_resources()
    if kg is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.icon("hourglass_empty", size="48px").props("color=primary")
            ui.label("Backend still initializing — please reload in a moment.").classes("text-lg mt-2")
        return None, None

    import app_state
    from ..settings import install_dark_mode, dark_toggle_button

    app_state.apply_colors()
    dm = install_dark_mode()

    # ── Top bar ──
    with ui.card().classes(
        "w-full flex-row items-center justify-between rounded-none px-5 py-3"
    ).props("flat bordered"):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense"
            ).tooltip("Back to projects")
            ui.label("Knowledge Graph").classes("text-sm font-bold leading-none")
        with ui.row().classes("gap-2 items-center"):
            dark_toggle_button(dm)

    # ── Left drawer ──
    with ui.left_drawer(value=True, fixed=True).props("width=260 bordered"):
        with ui.column().classes("w-full p-4 gap-2"):
            ui.label("Navigation").classes("text-xs font-bold uppercase tracking-widest opacity-50 mb-1")
            for path, label, icon in _PAGES:
                ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p)).props(
                    f"dense no-caps align=left {'' if path == active else 'outline'}"
                    + (" color=primary" if path == active else " color=grey-7")
                ).classes("w-full justify-start")

            ui.separator().classes("my-2")

            # Statistics (cached — fetched once)
            ui.label("Statistics").classes("text-xs font-bold uppercase tracking-widest opacity-50 mb-1")
            stats = kg.stats()
            for label, key in [
                ("Nodes", "nodes_total"),
                ("Edges", "edges_total"),
                ("Terms", "node_term"),
                ("Mappings", "node_translation_mapping"),
                ("Concepts", "node_concept"),
                ("Agents", "node_agent"),
                ("Sources", "node_source_text"),
                ("Institutions", "node_institution"),
            ]:
                with ui.row().classes("w-full justify-between"):
                    ui.label(label).classes("text-xs opacity-70")
                    ui.label(str(stats.get(key, 0))).classes("text-xs font-mono")

            ui.separator().classes("my-2")

            async def _hard_save():
                from ..components import busy_overlay
                async with busy_overlay("Saving database…"):
                    await run.io_bound(kg.save)
                ui.notify("Database saved.", type="positive")
            ui.button("Hard Save to Disk", icon="save", on_click=_hard_save).props(
                "flat dense no-caps color=primary"
            ).classes("w-full")

    return kg, glossary


# ---------------------------------------------------------------------------
# Record rendering — used by extraction review page
# ---------------------------------------------------------------------------

def render_record(r: dict) -> None:
    """Render an extraction-review record as a card inside the current container."""
    p = r.get("payload", {})
    src = r.get("source", {})
    kind = r.get("kind", "")

    with ui.card().classes("w-full p-3 gap-1").props("flat bordered"):
        if kind == "cited_work":
            ui.label(f"Author: {p.get('author')}").classes("text-sm")
            ui.label(f"Title (orig): {p.get('title_orig') or p.get('title_en')}").classes("text-sm")
            ui.label(f"Title (translation): {p.get('title_translation') or p.get('title_sl')}").classes("text-sm")
            ui.label(f"Year: {p.get('year')}").classes("text-sm")
            op = p.get("original_pub") or {}
            if op:
                ui.label(f"Original pub: {op.get('city', '')} / {op.get('publisher', '')}").classes("text-sm")
            te = p.get("translation_edition") or p.get("slovenian_edition") or {}
            if te:
                ui.label(
                    f"Translation edition: {te.get('city', '')} / {te.get('publisher', '')} "
                    f"(trans. {te.get('translator', '—')})"
                ).classes("text-sm")
            ui.label(f"Cited in: {p.get('container_work_id')}").classes("text-sm")
        elif kind == "translated_work":
            ui.label(f"Author: {p.get('author')}").classes("text-sm")
            ui.label(f"Translator: {p.get('translator')}").classes("text-sm")
            ui.label(f"Title (orig): {p.get('title_orig') or p.get('title_en')}").classes("text-sm")
            ui.label(f"Title (translation): {p.get('title_translation') or p.get('title_sl')}").classes("text-sm")
            ui.label(f"Year: {p.get('year')}").classes("text-sm")
            ui.label(f"Project type: {p.get('project_type')}").classes("text-sm")
        elif kind == "agent_person":
            ui.label(f"Name: {p.get('name')}").classes("text-sm")
            ui.label(f"Roles: {p.get('all_roles', [p.get('role')])}").classes("text-sm")
            ui.label(f"Mention count: {p.get('mention_count', 1)}").classes("text-sm")
            ui.label(f"Dedup group: {p.get('dedup_group')}").classes("text-sm")
            alts = p.get("alt_spellings", [])
            if alts and len(alts) > 1:
                ui.label(f"Alt spellings: {alts}").classes("text-sm")
        elif kind == "institution":
            ui.label(f"Name: {p.get('name')}").classes("text-sm")
            ui.label(f"Kind: {p.get('kind')}").classes("text-sm")
            ui.label(f"City: {p.get('city')}").classes("text-sm")

        if src.get("src_excerpt"):
            ui.label(f"EN segment: {src['src_excerpt'][:240]}").classes("text-[11px] opacity-45")
        if src.get("tgt_excerpt"):
            ui.label(f"SL segment: {src['tgt_excerpt'][:240]}").classes("text-[11px] opacity-45")
        if r.get("reason_codes"):
            ui.label(f"Reasons: {r['reason_codes']}").classes("text-[11px] opacity-45")


# ---------------------------------------------------------------------------
# Reclassify inputs — used by review/kg-review pages
# ---------------------------------------------------------------------------

def build_reclass_inputs(target: str, c: dict) -> dict:
    """Build NiceGUI input elements for a reclassify form."""
    f: dict = {}

    if target == "agent":
        f["name"] = ui.input("Name:", value=c.get("name") or "")
        f["role"] = ui.select(RECLASS_AGENT_ROLES, value=RECLASS_AGENT_ROLES[0], label="Role:").classes("w-full")
    elif target == "cited_work":
        f["project_type"] = ui.select(
            RECLASS_PROJECT_TYPES, value=RECLASS_PROJECT_TYPES[0], label="Project type:",
        ).classes("w-full")
        f["title_orig"] = ui.input("Title (orig):", value=c.get("title_orig") or c.get("primary") or "")
        f["title_translation"] = ui.input("Title (translation):", value=c.get("title_translation") or "")
        f["year"] = ui.input("Year:", value=str(c.get("year") or ""))
        f["author"] = ui.input("Author (optional):", value=c.get("author") or "")
        f["city"] = ui.input("City (optional):", value=c.get("city") or "")
    elif target == "concept":
        f["label"] = ui.input("Label:", value=c.get("primary") or "")
        f["domain"] = ui.input("Domain:", value="").props('placeholder="e.g. visual-art, performance, humanities"')
        f["definition"] = ui.textarea("Definition (optional):", value="")
    elif target == "term":
        f["term"] = ui.input("Term:", value=c.get("primary") or "")
        f["lang"] = ui.select(["en", "sl"], value="en", label="Language:").classes("w-full")
    elif target == "institution":
        f["name"] = ui.input("Name:", value=c.get("name") or c.get("primary") or "")
        f["kind"] = ui.select(INSTITUTION_KINDS, value="publisher", label="Kind:").classes("w-full")
        f["city"] = ui.input("City (optional):", value=c.get("city") or "")

    return f


# ---------------------------------------------------------------------------
# Predicates moved from deleted concepts.py and sources.py
# (kept for backward compatibility with tests and potential reuse)
# ---------------------------------------------------------------------------


def needs_target(c: dict) -> bool:
    """A concept needs a target label if it has a translation_lang slot set
    but no label_translation yet."""
    return bool(c.get("translation_lang")) and not (c.get("label_translation") or "").strip()


def has_translation(s: dict, has_translator: bool) -> bool:
    """A source has a known published translation iff it has translation_edition,
    a title_translation, or a translated_by edge."""
    if s.get("translation_edition"):
        return True
    if (s.get("title_translation") or "").strip():
        return True
    return has_translator