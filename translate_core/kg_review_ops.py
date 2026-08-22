"""Framework-pure KG review operations — shared between Streamlit and NiceGUI."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from config import BASE_DIR
from translate_core.entity_extraction.name_dedup import dedup_group_key
from translate_core.kg_ingest_entities import _atomic_write_text

# ---------------------------------------------------------------------------
# Data paths (re-anchored on config.BASE_DIR / "data" / ...)
# ---------------------------------------------------------------------------
REVIEW_PATH = BASE_DIR / "data" / "extraction_review.json"
PREVIEW_PATH = BASE_DIR / "data" / "extraction_pattern_preview.md"
KG_REVIEW_PATH = BASE_DIR / "data" / "kg_review.json"
KG_DISMISSED_PATH = BASE_DIR / "data" / "kg_review_dismissed.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AGENT_ROLES = [
    "author", "translator", "editor", "curator", "artist",
    "interviewer", "interviewee", "choreographer", "director",
    "performer", "dancer", "composer", "dramaturg", "agent",
]
# Reviewer reclassify uses the same canonical set.
RECLASS_AGENT_ROLES = AGENT_ROLES
RECLASS_PROJECT_TYPES = [
    "cited_work", "book", "book_chapter", "journal_article",
    "magazine_article", "newspaper_article", "web_source",
    "exhibition_catalog", "interview", "thesis_dissertation", "artwork",
]
INSTITUTION_KINDS = [
    "publisher", "gallery", "museum", "university", "festival",
    "theatre", "journal", "organization", "sponsor", "country", "other",
]
# Reclassification target -> menu label.
RECLASS_TARGETS = {
    "agent": "Agent (person)",
    "cited_work": "Work / source text",
    "institution": "Institution",
    "concept": "Concept",
    "term": "Term",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def record_matches_text(r: dict, q: str) -> bool:
    p = r.get("payload", {})
    haystack = " ".join(str(v) for v in [
        p.get("name"), p.get("author"),
        p.get("title_orig"), p.get("title_translation"),
    ] if v).lower()
    return q in haystack


def record_label(r: dict) -> str:
    p = r.get("payload", {})
    if r["kind"] == "cited_work":
        return f"{p.get('author', '?')} — {(p.get('title_orig') or p.get('title_translation') or '?')[:60]} ({p.get('year') or '—'})"
    if r["kind"] == "translated_work":
        return f"{p.get('author', '?')} — {(p.get('title_orig') or p.get('title_translation') or '?')[:60]} ({p.get('year') or '—'})"
    if r["kind"] == "agent_person":
        return f"{p.get('name', '?')} (×{p.get('mention_count', 1)}, group={p.get('dedup_group')})"
    if r["kind"] == "institution":
        return f"{p.get('name', '?')} ({p.get('kind', '?')}, {p.get('city') or '—'})"
    return str(p)[:80]


def review_slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] if s else "unknown"


# ---------------------------------------------------------------------------
# Review-tier commit
# ---------------------------------------------------------------------------
def commit_record(kg, r: dict) -> str | None:
    """Write a single review-tier record to the KG.
    Returns None on success, or an error string if the record cannot be
    committed (e.g. translated_work without a translator — O-20)."""
    p = r["payload"]
    kind = r["kind"]
    if kind == "agent_person":
        agent_id = review_slugify(p["name"])
        kg.add_agent_node(
            agent_id,
            name=p["name"],
            role=p["role"] if p.get("role") != "multi" else "author",
            dedup_group=p.get("dedup_group"),
            alt_spellings=p.get("alt_spellings", []),
            all_roles=p.get("all_roles", [p.get("role")]),
            mention_count=p.get("mention_count", 1),
        )
    elif kind == "institution":
        inst_id = review_slugify(p["name"])
        kg.add_institution_node(
            inst_id, name=p["name"], kind=p.get("kind", "publisher"),
            city=p.get("city"),
        )
    elif kind == "translated_work":
        translator = (p.get("translator") or "").strip()
        if not translator:
            return "Cannot commit a translated_work without a translator (O-20). Add a translator name first."
        wid = p["work_id"]
        year = p.get("year")
        try:
            year_int = int(year) if year else None
        except (TypeError, ValueError):
            year_int = None
        kg.add_source_text_node(
            wid,
            title=p.get("title_orig") or p.get("title_translation") or wid,
            year=year_int,
            title_orig=p.get("title_orig"),
            title_translation=p.get("title_translation"),
            orig_lang=p.get("orig_lang"),
            translation_lang=p.get("translation_lang"),
            project_type=p.get("project_type", "book_translation"),
        )
        if p.get("author"):
            aid = review_slugify(p["author"])
            if not kg.G.has_node(f"agent:{aid.lower()}"):
                kg.add_agent_node(aid, name=p["author"], role="author")
            kg.link_written_by(wid, aid)
        if p.get("translator"):
            tid = review_slugify(p["translator"])
            if not kg.G.has_node(f"agent:{tid.lower()}"):
                kg.add_agent_node(tid, name=p["translator"], role="translator")
            kg.link_translated_by(wid, tid)
    elif kind == "cited_work":
        cid = p["cited_id"]
        year = p.get("year")
        try:
            year_int = int(year) if year else None
        except (TypeError, ValueError):
            year_int = None
        kg.add_source_text_node(
            cid,
            title=p.get("title_orig") or p.get("title_translation") or cid,
            year=year_int,
            title_orig=p.get("title_orig"),
            title_translation=p.get("title_translation"),
            orig_lang=p.get("orig_lang"),
            translation_lang=p.get("translation_lang"),
            project_type="cited_work",
            translation_edition=p.get("translation_edition"),
        )
        pub = p.get("original_pub") or {}
        if p.get("author"):
            aid = review_slugify(p["author"])
            if not kg.G.has_node(f"agent:{aid.lower()}"):
                kg.add_agent_node(aid, name=p["author"], role="author")
            kg.link_written_by(cid, aid)
        if pub.get("publisher"):
            iid = review_slugify(pub["publisher"])
            if not kg.G.has_node(f"institution:{iid.lower()}"):
                kg.add_institution_node(
                    iid, name=pub["publisher"], kind="publisher",
                    city=pub.get("city"),
                )
            kg.link_published_by(cid, iid)


# ---------------------------------------------------------------------------
# Candidate texts / reclassify
def candidate_texts(r: dict) -> dict:
    """Best-guess text values from a record's payload, for pre-filling a
    reclassification form so the reviewer only confirms/edits."""
    p = r.get("payload", {})
    primary = (p.get("name") or p.get("title_orig") or p.get("title_translation")
               or p.get("title_en") or p.get("title_sl")
               or p.get("author") or "")
    return {
        "primary": primary,
        "name": p.get("name") or p.get("author") or primary,
        "title_orig": p.get("title_orig") or p.get("title_en") or "",
        "title_translation": p.get("title_translation") or p.get("title_sl") or "",
        "author": p.get("author") or "",
        "year": p.get("year"),
        "city": p.get("city") or "",
    }


def commit_as(kg, target: str, f: dict) -> tuple[str | None, str | None]:
    """Create a node of `target` type from reviewer-confirmed fields.
    Returns (error_message, new_node_id): on success (None, id); on a missing
    required field (message, None)."""
    if target == "agent":
        name = (f.get("name") or "").strip()
        if not name:
            return "Name is required.", None
        role = f.get("role", "author")
        new_id = kg.add_agent_node(
            review_slugify(name), name=name, role=role,
            dedup_group=dedup_group_key(name),
            alt_spellings=[name], all_roles=[role], mention_count=1,
        )
    elif target == "cited_work":
        title = (f.get("title_orig") or f.get("title_translation") or "").strip()
        if not title:
            return "A title is required.", None
        try:
            year_int = int(f["year"]) if (f.get("year") or "").strip() else None
        except (TypeError, ValueError):
            year_int = None
        cid = review_slugify(f.get("title_orig") or f.get("title_translation"))
        new_id = kg.add_source_text_node(
            cid, title=title, year=year_int,
            title_orig=(f.get("title_orig") or "").strip() or None,
            title_translation=(f.get("title_translation") or "").strip() or None,
            orig_lang=f.get("orig_lang") or None,
            translation_lang=f.get("translation_lang") or None,
            project_type=f.get("project_type", "cited_work"),
        )
        author = (f.get("author") or "").strip()
        if author:
            aid = review_slugify(author)
            if not kg.G.has_node(f"agent:{aid.lower()}"):
                kg.add_agent_node(
                    aid, name=author, role="author",
                    dedup_group=dedup_group_key(author),
                    alt_spellings=[author], all_roles=["author"], mention_count=1,
                )
            kg.link_written_by(cid, aid)
    elif target == "concept":
        label = (f.get("label") or "").strip()
        if not label:
            return "Label is required.", None
        new_id = kg.add_concept_node(
            f"concept:{review_slugify(label)}", label=label,
            domain=(f.get("domain") or "").strip(), definition=(f.get("definition") or "").strip(),
        )
    elif target == "term":
        term = (f.get("term") or "").strip()
        if not term:
            return "Term is required.", None
        new_id = kg.add_term_node(term, f.get("lang", "en"), is_phrase=(" " in term))
    elif target == "institution":
        name = (f.get("name") or "").strip()
        if not name:
            return "Name is required for institution.", None
        kind = (f.get("kind") or "publisher").strip()
        inst_id = f"institution:{review_slugify(name)}"
        new_id = kg.add_institution_node(inst_id, name, kind=kind, city=(f.get("city") or "").strip() or None)
    else:
        return f"Unknown target type: {target}", None
    return None, new_id


# ---------------------------------------------------------------------------
# Queue I/O
# ---------------------------------------------------------------------------
def drop_from_queue(review_records: list, r: dict) -> None:
    """Persist the review queue with record `r` removed."""
    remaining = [x for x in review_records if x is not r]
    _atomic_write_text(
        REVIEW_PATH,
        json.dumps(remaining, ensure_ascii=False, indent=2, default=str),
    )


def reclassify_live_node(kg, old_id: str, target: str, fields: dict) -> str | None:
    """Create a new node of `target` type from `fields`, migrate the old node's
    edges onto it, and remove the old node. Returns an error string or None."""
    err, new_id = commit_as(kg, target, fields)
    if err:
        return err
    if not new_id or not kg.G.has_node(old_id):
        return None
    if new_id != old_id:
        kg.reclassify_node(old_id, new_id)
    return None


def load_kg_review() -> list[dict]:
    if KG_REVIEW_PATH.exists():
        try:
            return json.loads(KG_REVIEW_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def drop_kg_review(items: list[dict], item: dict, *, dismiss: bool = False) -> None:
    """Remove `item` from the live-review queue; if dismiss, also remember its id
    so a future scan does not re-flag it."""
    remaining = [x for x in items if x.get("id") != item.get("id")]
    _atomic_write_text(KG_REVIEW_PATH, json.dumps(remaining, ensure_ascii=False, indent=2))
    if dismiss:
        try:
            cur = set(json.loads(KG_DISMISSED_PATH.read_text(encoding="utf-8"))) \
                if KG_DISMISSED_PATH.exists() else set()
        except Exception:
            cur = set()
        cur.add(item.get("id"))
        _atomic_write_text(KG_DISMISSED_PATH, json.dumps(sorted(cur), ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Paths
    "REVIEW_PATH",
    "PREVIEW_PATH",
    "KG_REVIEW_PATH",
    "KG_DISMISSED_PATH",
    # Constants
    "AGENT_ROLES",
    "RECLASS_AGENT_ROLES",
    "RECLASS_PROJECT_TYPES",
    "INSTITUTION_KINDS",
    "RECLASS_TARGETS",
    # Functions
    "record_matches_text",
    "record_label",
    "review_slugify",
    "commit_record",
    "candidate_texts",
    "commit_as",
    "drop_from_queue",
    "reclassify_live_node",
    "load_kg_review",
    "drop_kg_review",
]