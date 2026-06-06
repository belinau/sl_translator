# kg_editor_ui.py
#
# Knowledge Graph Curation Workspace
# Search-first design: search → select → edit. Never renders all entities at once.
#
# Architecture (Streamlit 1.36+):
#   * Multipage via st.navigation/st.Page (function pages) — ONLY the selected
#     page's body executes per rerun, instead of st.tabs running all six bodies
#     on every keystroke.
#   * Every expensive full-graph scan (stats, get_all_by_type, the written_by
#     edge index, lineages) is memoized with @st.cache_data keyed on a
#     `kg_version` token. The token bumps on every mutation, so caches stay
#     correct while interactions that don't change the graph cost ~nothing.
#

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Direct module imports — bypass translate_core/__init__.py
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, str(_BASE / path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_config = _load_module("config", "config.py")
_kg_mod = _load_module("translate_core.knowledge_graph", "translate_core/knowledge_graph.py")
_gl_mod = _load_module("translate_core.glossary", "translate_core/glossary.py")

KnowledgeGraph = _kg_mod.KnowledgeGraph
Glossary = _gl_mod.Glossary

# name_dedup is stdlib-only — safe to load standalone for dedup_group_key, which
# every new agent write needs (ontology O-12).
_nd_mod = _load_module(
    "translate_core.entity_extraction.name_dedup",
    "translate_core/entity_extraction/name_dedup.py",
)
dedup_group_key = _nd_mod.dedup_group_key

# Live-KG dubious-node flagger (stdlib-only, safe to load standalone).
_flag_mod = _load_module("scripts.flag_kg_review", "scripts/flag_kg_review.py")
flag_dubious = _flag_mod.flag_dubious


@st.cache_resource
def load_kg():
    return KnowledgeGraph()


@st.cache_resource
def load_glossary():
    return Glossary()


kg = load_kg()
glossary = load_glossary()

# ---------------------------------------------------------------------------
# Version token: invalidates @st.cache_data when the graph changes.
# ---------------------------------------------------------------------------
if "kg_version" not in st.session_state:
    st.session_state.kg_version = 0


def _version() -> int:
    return st.session_state.kg_version


def _rerun_fresh() -> None:
    """Bump the cache token (so derived data recomputes) and rerun. Call after
    any KG mutation instead of bare st.rerun()."""
    st.session_state.kg_version += 1
    st.rerun()


# ---------------------------------------------------------------------------
# Cached derivations — each is a single O(N) pass, reused until the next write.
# They reference the cached `kg`/`glossary` singletons and key only on version.
# ---------------------------------------------------------------------------
@st.cache_data(max_entries=4, show_spinner=False)
def cached_stats(version: int) -> dict:
    return kg.stats()


@st.cache_data(max_entries=4, show_spinner=False)
def cached_by_type(version: int, node_type: str) -> list[dict]:
    return kg.get_all_by_type(node_type)


@st.cache_data(max_entries=4, show_spinner=False)
def cached_agent_opts(version: int) -> dict[str, str]:
    return {a["id"]: a.get("name", a["id"]) for a in kg.get_all_by_type("agent")}


@st.cache_data(max_entries=4, show_spinner=False)
def cached_src_author(version: int) -> dict[str, str]:
    """source_id -> first written_by author agent id. One pass over edges."""
    out: dict[str, str] = {}
    for u, v, d in kg.G.edges(data=True):
        if d.get("relation") == "written_by":
            out.setdefault(u, v)
    return out


@st.cache_data(max_entries=4, show_spinner=False)
def cached_lineages(version: int) -> list[str]:
    return kg.get_all_lineages()


# ---------------------------------------------------------------------------
# Review-tab record helpers (Streamlit re-runs top-to-bottom; defined once).
# ---------------------------------------------------------------------------
REVIEW_PATH = Path("data/extraction_review.json")
PREVIEW_PATH = Path("data/extraction_pattern_preview.md")
KG_REVIEW_PATH = Path("data/kg_review.json")
KG_DISMISSED_PATH = Path("data/kg_review_dismissed.json")
AGENT_ROLES = ["author", "translator", "editor", "interviewer", "curator", "organization"]
# Targets a reviewer can reclassify a record into (ontology node types).
RECLASS_AGENT_ROLES = ["author", "translator", "editor", "curator", "artist",
                       "interviewer", "interviewee", "agent"]
RECLASS_PROJECT_TYPES = ["cited_work", "book", "book_chapter", "journal_article",
                         "magazine_article", "newspaper_article", "web_source",
                         "exhibition_catalog", "interview", "thesis_dissertation", "artwork"]
INSTITUTION_KINDS = ["publisher", "gallery", "museum", "university", "festival",
                     "theatre", "journal", "organization", "sponsor", "country", "other"]
# Reclassification target -> menu label.
RECLASS_TARGETS = {
    "agent": "Agent (person)",
    "cited_work": "Work / source text",
    "institution": "Institution",
    "concept": "Concept",
    "term": "Term",
}


def _record_matches_text(r: dict, q: str) -> bool:
    p = r.get("payload", {})
    haystack = " ".join(str(v) for v in [
        p.get("name"), p.get("author"),
        p.get("title_en"), p.get("title_sl"), p.get("title_orig"),
    ] if v).lower()
    return q in haystack


def _record_label(r: dict) -> str:
    p = r.get("payload", {})
    if r["kind"] == "cited_work":
        return f"{p.get('author', '?')} — {(p.get('title_en') or p.get('title_sl') or '?')[:60]} ({p.get('year') or '—'})"
    if r["kind"] == "translated_work":
        return f"{p.get('author', '?')} — {(p.get('title_en') or '?')[:60]} ({p.get('year') or '—'})"
    if r["kind"] == "agent_person":
        return f"{p.get('name', '?')} (×{p.get('mention_count', 1)}, group={p.get('dedup_group')})"
    if r["kind"] == "institution":
        return f"{p.get('name', '?')} ({p.get('kind', '?')}, {p.get('city') or '—'})"
    return str(p)[:80]


def _render_record(r: dict) -> None:
    p = r.get("payload", {})
    src = r.get("source", {})
    if r["kind"] == "cited_work":
        st.markdown(f"**Author:** `{p.get('author')}`")
        st.markdown(f"**Title (EN):** `{p.get('title_en')}`")
        st.markdown(f"**Title (SL):** `{p.get('title_sl')}`")
        if p.get("title_orig"):
            st.markdown(f"**Title (orig):** `{p.get('title_orig')}`")
        st.markdown(f"**Year:** `{p.get('year')}`")
        op = p.get("original_pub") or {}
        if op:
            st.markdown(f"**Original pub:** {op.get('city', '')} / {op.get('publisher', '')}")
        sp = p.get("slovenian_edition") or {}
        if sp:
            st.markdown(f"**SL edition:** {sp.get('city', '')} / {sp.get('publisher', '')} (trans. {sp.get('translator', '—')})")
        st.markdown(f"**Cited in:** `{p.get('container_work_id')}`")
    elif r["kind"] == "translated_work":
        st.markdown(f"**Author:** `{p.get('author')}`")
        st.markdown(f"**Translator:** `{p.get('translator')}`")
        st.markdown(f"**Title (EN):** `{p.get('title_en')}`")
        st.markdown(f"**Title (SL):** `{p.get('title_sl')}`")
        st.markdown(f"**Year:** `{p.get('year')}`")
        st.markdown(f"**Project type:** `{p.get('project_type')}`")
    elif r["kind"] == "agent_person":
        st.markdown(f"**Name:** `{p.get('name')}`")
        st.markdown(f"**Roles:** `{p.get('all_roles', [p.get('role')])}`")
        st.markdown(f"**Mention count:** `{p.get('mention_count', 1)}`")
        st.markdown(f"**Dedup group:** `{p.get('dedup_group')}`")
        alts = p.get("alt_spellings", [])
        if alts and len(alts) > 1:
            st.markdown(f"**Alt spellings:** `{alts}`")
    elif r["kind"] == "institution":
        st.markdown(f"**Name:** `{p.get('name')}`")
        st.markdown(f"**Kind:** `{p.get('kind')}`")
        st.markdown(f"**City:** `{p.get('city')}`")
    if src.get("src_excerpt"):
        st.caption(f"EN segment: `{src['src_excerpt'][:240]}`")
    if src.get("tgt_excerpt"):
        st.caption(f"SL segment: `{src['tgt_excerpt'][:240]}`")
    if r.get("reason_codes"):
        st.caption(f"Reasons: {r['reason_codes']}")


def _review_slugify(text: str) -> str:
    import re as _re
    import unicodedata as _u
    nfkd = _u.normalize("NFKD", text)
    s = "".join(c for c in nfkd if not _u.combining(c))
    s = _re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] if s else "unknown"


def _commit_record(kg, r: dict) -> None:
    """Write a single review-tier record to the KG."""
    p = r["payload"]
    kind = r["kind"]
    if kind == "agent_person":
        agent_id = _review_slugify(p["name"])
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
        inst_id = _review_slugify(p["name"])
        kg.add_institution_node(
            inst_id, name=p["name"], kind=p.get("kind", "publisher"),
            city=p.get("city"),
        )
    elif kind == "translated_work":
        wid = p["work_id"]
        year = p.get("year")
        try:
            year_int = int(year) if year else None
        except (TypeError, ValueError):
            year_int = None
        kg.add_source_text_node(
            wid,
            title=p.get("title_en") or p.get("title_sl") or wid,
            year=year_int,
            title_en=p.get("title_en"),
            title_sl=p.get("title_sl"),
            project_type=p.get("project_type", "book_translation"),
        )
        if p.get("author"):
            aid = _review_slugify(p["author"])
            if not kg.G.has_node(f"agent:{aid.lower()}"):
                kg.add_agent_node(aid, name=p["author"], role="author")
            sn = f"source:{wid.lower()}"
            an = f"agent:{aid.lower()}"
            if kg.G.has_node(sn) and kg.G.has_node(an) and not kg.G.has_edge(sn, an):
                kg.G.add_edge(sn, an, relation="written_by")
        if p.get("translator"):
            tid = _review_slugify(p["translator"])
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
            title=p.get("title_en") or p.get("title_sl") or p.get("title_orig") or cid,
            year=year_int,
            title_en=p.get("title_en"),
            title_sl=p.get("title_sl"),
            title_orig=p.get("title_orig"),
            project_type="cited_work",
            slovenian_edition=p.get("slovenian_edition"),
        )
        if p.get("author"):
            aid = _review_slugify(p["author"])
            if not kg.G.has_node(f"agent:{aid.lower()}"):
                kg.add_agent_node(aid, name=p["author"], role="author")
            sn = f"source:{cid.lower()}"
            an = f"agent:{aid.lower()}"
            if kg.G.has_node(sn) and kg.G.has_node(an) and not kg.G.has_edge(sn, an):
                kg.G.add_edge(sn, an, relation="written_by")
        if p.get("container_work_id"):
            kg.link_cited_in(cid, p["container_work_id"])
        pub = p.get("original_pub") or {}
        if pub.get("publisher"):
            iid = _review_slugify(pub["publisher"])
            if not kg.G.has_node(f"institution:{iid.lower()}"):
                kg.add_institution_node(
                    iid, name=pub["publisher"], kind="publisher",
                    city=pub.get("city"),
                )
            kg.link_published_by(cid, iid)


def _candidate_texts(r: dict) -> dict:
    """Best-guess text values from a record's payload, for pre-filling a
    reclassification form so the reviewer only confirms/edits."""
    p = r.get("payload", {})
    primary = (p.get("name") or p.get("title_en") or p.get("title_sl")
               or p.get("title_orig") or p.get("author") or "")
    return {
        "primary": primary,
        "name": p.get("name") or p.get("author") or primary,
        "title_en": p.get("title_en") or "",
        "title_sl": p.get("title_sl") or "",
        "author": p.get("author") or "",
        "year": p.get("year"),
        "city": p.get("city") or "",
    }


def _reclass_inputs(target: str, c: dict, key: str) -> dict:
    """Render target-specific inputs (pre-filled from candidate text) and
    return the collected field values. Call inside an st.form."""
    f: dict = {}
    if target == "agent":
        f["name"] = st.text_input("Name:", value=c["name"], key=f"{key}_name")
        f["role"] = st.selectbox("Role:", RECLASS_AGENT_ROLES, key=f"{key}_role")
    elif target == "cited_work":
        f["project_type"] = st.selectbox("Project type:", RECLASS_PROJECT_TYPES, key=f"{key}_pt")
        f["title_en"] = st.text_input("Title (EN):", value=c["title_en"] or c["primary"], key=f"{key}_ten")
        f["title_sl"] = st.text_input("Title (SL):", value=c["title_sl"], key=f"{key}_tsl")
        f["year"] = st.text_input("Year:", value=str(c["year"] or ""), key=f"{key}_yr")
        f["author"] = st.text_input("Author (optional):", value=c["author"], key=f"{key}_au")
    elif target == "institution":
        f["name"] = st.text_input("Name:", value=c["primary"], key=f"{key}_iname")
        f["kind"] = st.selectbox("Kind:", INSTITUTION_KINDS, key=f"{key}_ikind")
        f["city"] = st.text_input("City (optional):", value=c["city"], key=f"{key}_icity")
    elif target == "concept":
        f["label"] = st.text_input("Label:", value=c["primary"], key=f"{key}_clabel")
        f["domain"] = st.text_input("Domain:", value="", key=f"{key}_cdom",
                                    placeholder="e.g. visual-art, performance, humanities")
        f["definition"] = st.text_area("Definition (optional):", value="", key=f"{key}_cdef")
    elif target == "term":
        f["term"] = st.text_input("Term:", value=c["primary"], key=f"{key}_tterm")
        f["lang"] = st.selectbox("Language:", ["en", "sl"], key=f"{key}_tlang")
    return f


def _commit_as(kg, target: str, f: dict) -> tuple[str | None, str | None]:
    """Create a node of `target` type from reviewer-confirmed fields.
    Returns (error_message, new_node_id): on success (None, id); on a missing
    required field (message, None)."""
    if target == "agent":
        name = (f.get("name") or "").strip()
        if not name:
            return "Name is required.", None
        role = f.get("role", "author")
        new_id = kg.add_agent_node(
            _review_slugify(name), name=name, role=role,
            dedup_group=dedup_group_key(name),
            alt_spellings=[name], all_roles=[role], mention_count=1,
        )
    elif target == "cited_work":
        title = (f.get("title_en") or f.get("title_sl") or "").strip()
        if not title:
            return "A title (EN or SL) is required.", None
        try:
            year_int = int(f["year"]) if (f.get("year") or "").strip() else None
        except (TypeError, ValueError):
            year_int = None
        cid = _review_slugify(f.get("title_en") or f.get("title_sl"))
        new_id = kg.add_source_text_node(
            cid, title=title, year=year_int,
            title_en=(f.get("title_en") or "").strip() or None,
            title_sl=(f.get("title_sl") or "").strip() or None,
            project_type=f.get("project_type", "cited_work"),
        )
        author = (f.get("author") or "").strip()
        if author:
            aid = _review_slugify(author)
            if not kg.G.has_node(f"agent:{aid.lower()}"):
                kg.add_agent_node(
                    aid, name=author, role="author",
                    dedup_group=dedup_group_key(author),
                    alt_spellings=[author], all_roles=["author"], mention_count=1,
                )
            sn, an = f"source:{cid.lower()}", f"agent:{aid.lower()}"
            if kg.G.has_node(sn) and kg.G.has_node(an) and not kg.G.has_edge(sn, an):
                kg.G.add_edge(sn, an, relation="written_by")
    elif target == "institution":
        name = (f.get("name") or "").strip()
        if not name:
            return "Name is required.", None
        new_id = kg.add_institution_node(
            _review_slugify(name), name=name,
            kind=f.get("kind", "publisher"), city=(f.get("city") or "").strip() or None,
        )
    elif target == "concept":
        label = (f.get("label") or "").strip()
        if not label:
            return "Label is required.", None
        new_id = kg.add_concept_node(
            f"concept:{_review_slugify(label)}", label=label,
            domain=(f.get("domain") or "").strip(), definition=(f.get("definition") or "").strip(),
        )
    elif target == "term":
        term = (f.get("term") or "").strip()
        if not term:
            return "Term is required.", None
        new_id = kg.add_term_node(term, f.get("lang", "en"), is_phrase=(" " in term))
    else:
        return f"Unknown target type: {target}", None
    return None, new_id


def _drop_from_queue(review_records: list, r: dict) -> None:
    """Persist the review queue with record `r` removed."""
    remaining = [x for x in review_records if x is not r]
    REVIEW_PATH.write_text(
        json.dumps(remaining, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )


def _reclassify_live_node(kg, old_id: str, target: str, fields: dict) -> str | None:
    """Create a new node of `target` type from `fields`, migrate the old node's
    edges onto it, and remove the old node. Returns an error string or None."""
    err, new_id = _commit_as(kg, target, fields)
    if err:
        return err
    if not new_id or not kg.G.has_node(old_id):
        return None
    if new_id != old_id:
        for u, _v, d in list(kg.G.in_edges(old_id, data=True)):
            if u != new_id and not kg.G.has_edge(u, new_id):
                kg.G.add_edge(u, new_id, **d)
        for _u, v, d in list(kg.G.out_edges(old_id, data=True)):
            if v != new_id and not kg.G.has_edge(new_id, v):
                kg.G.add_edge(new_id, v, **d)
        kg.remove_node(old_id)
    return None


def _load_kg_review() -> list[dict]:
    if KG_REVIEW_PATH.exists():
        try:
            return json.loads(KG_REVIEW_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _drop_kg_review(items: list[dict], item: dict, *, dismiss: bool = False) -> None:
    """Remove `item` from the live-review queue; if dismiss, also remember its id
    so a future scan does not re-flag it."""
    remaining = [x for x in items if x.get("id") != item.get("id")]
    KG_REVIEW_PATH.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
    if dismiss:
        try:
            cur = set(json.loads(KG_DISMISSED_PATH.read_text(encoding="utf-8"))) \
                if KG_DISMISSED_PATH.exists() else set()
        except Exception:
            cur = set()
        cur.add(item.get("id"))
        KG_DISMISSED_PATH.write_text(json.dumps(sorted(cur), ensure_ascii=False, indent=2), encoding="utf-8")


# ===========================================================================
# PAGE: Terms & Mappings (search-first)
# ===========================================================================
def page_terms():
    st.subheader("Search Terms & Edit Mappings")
    query = st.text_input("Search term (English or Slovenian):", key="term_search").strip()

    if query:
        matches = kg.extract_entities(query, target_lang="sl")
        if not matches:
            st.warning(f"No terms matching '{query}'.")
        else:
            st.caption(f"{len(matches)} terms found. Click a term to expand.")
            for match in matches:
                term_id = match.get("id")
                with st.expander(
                    f"`{match.get('term')}` ({match.get('lang', '?').upper()}) — "
                    f"freq {match.get('frequency', 0)}"
                ):
                    st.caption(f"ID: `{term_id}`  |  Animate: {match.get('is_animate', False)}")

                    with st.form(f"term_edit_{term_id}"):
                        cur_display = match.get("display_form", "") or ""
                        cur_animate = match.get("is_animate", False)
                        cur_phrase = match.get("is_phrase", False)
                        e_df = st.text_input("Display form:", value=cur_display)
                        e_anim = st.checkbox("Animate", value=cur_animate)
                        e_phrase = st.checkbox("Phrase", value=cur_phrase)
                        if st.form_submit_button("Update Term"):
                            kg.update_term_node(term_id, display_form=e_df or None,
                                                is_animate=e_anim, is_phrase=e_phrase)
                            st.success("Term updated.")
                            _rerun_fresh()

                    if st.button("🗑️ Delete this term", key=f"del_{term_id}"):
                        kg.remove_node(term_id)
                        kg._rebuild_indices()
                        st.success("Deleted.")
                        _rerun_fresh()

                    variants = match.get("variants", [])
                    if variants:
                        st.markdown(f"**Variants:** {', '.join(variants)}")

                    translations = match.get("translations", [])
                    if not translations:
                        st.info("No mappings.")
                    else:
                        st.markdown(f"**{len(translations)} translations:**")
                        for t in translations:
                            mapping_id = t.get("mapping_id")
                            lineage = t.get("lineage", "general")
                            st.markdown(
                                f"→ **`{t.get('term')}`** | Lineage: *{lineage}* | "
                                f"Conf: `{t.get('confidence', 0.5):.2f}`"
                            )
                            if t.get("sources") or t.get("agents"):
                                st.caption(
                                    f"Context: {', '.join(t.get('sources', []) + t.get('agents', []))}"
                                )
                            if mapping_id and kg.G.has_node(mapping_id):
                                with st.form(f"map_edit_{mapping_id}"):
                                    m_lin = st.text_input("Lineage:", value=lineage, key=f"ml_{mapping_id}")
                                    m_reg = st.selectbox(
                                        "Register:", ["academic", "manifesto", "poetic", "colloquial"],
                                        index=["academic", "manifesto", "poetic", "colloquial"].index(
                                            t.get("register", "academic")),
                                        key=f"mr_{mapping_id}",
                                    )
                                    m_gloss = st.text_input("Gloss:", value=t.get("gloss", ""), key=f"mg_{mapping_id}")
                                    m_conf = st.slider("Confidence:", 0.0, 1.0,
                                                       float(t.get("confidence", 0.5)), step=0.05,
                                                       key=f"mc_{mapping_id}")
                                    c1, c2 = st.columns(2)
                                    with c1:
                                        if st.form_submit_button("Update"):
                                            kg.update_translation_mapping(
                                                mapping_id, lineage=m_lin, register=m_reg,
                                                gloss=m_gloss, confidence=m_conf,
                                            )
                                            st.success("Updated.")
                                            _rerun_fresh()
                                    with c2:
                                        if st.form_submit_button("🗑️ Delete"):
                                            kg.remove_node(mapping_id)
                                            st.success("Deleted.")
                                            _rerun_fresh()
                            else:
                                st.caption("_Legacy edge (no mapping node) — not editable._")
    else:
        st.info("Type a search query above to find and edit terms and their mappings.")

    st.markdown("---")
    with st.expander("⚡ Quick-Add Translation Mapping"):
        with st.form("quick_link_form"):
            q_src = st.text_input("Source term (en):", placeholder="e.g. gaze").strip()
            q_tgt = st.text_input("Target term (sl):", placeholder="e.g. pogled").strip()
            q_lin = st.text_input("Lineage:", placeholder="e.g. Mulveyan").strip()
            q_gloss = st.text_input("Gloss:", placeholder="optional note").strip()
            if st.form_submit_button("Create Mapping", use_container_width=True):
                if q_src and q_tgt:
                    cid = kg.add_concept_node(
                        f"concept:{q_src.replace(' ', '_').lower()}", label=q_src, domain="General",
                    )
                    sid = kg.add_term_node(q_src, "en", concept_id=cid, is_phrase=True)
                    tid = kg.add_term_node(q_tgt, "sl", is_phrase=True)
                    kg.link_translations_with_context(
                        src_term_id=sid, tgt_term_id=tid,
                        confidence=1.0, lineage=q_lin or "general", gloss=q_gloss, verified=True,
                    )
                    st.success(f"Created: '{q_src}' → '{q_tgt}'.")
                    _rerun_fresh()
                else:
                    st.error("Both source and target terms required.")

    with st.expander("🏷️ Add Variant to Term"):
        var_search = st.text_input("Find term:", key="var_search").strip().lower()
        if var_search:
            all_terms = cached_by_type(_version(), "term")
            hits = [d for d in all_terms if var_search in d.get("term", "").lower()]
            if hits:
                choices = {d["id"]: f"{d.get('term')} ({d.get('lang')})" for d in hits}
                with st.form("add_variant_form"):
                    sel = st.selectbox("Term:", options=list(choices.keys()),
                                       format_func=lambda x: choices.get(x, x))
                    var_text = st.text_input("Variant form:").strip()
                    if st.form_submit_button("Add Variant"):
                        if sel and var_text:
                            kg.add_variant(sel, var_text)
                            st.success(f"Added '{var_text}'.")
                            _rerun_fresh()
            else:
                st.info(f"No terms matching '{var_search}'.")


# ===========================================================================
# PAGE: Concepts
# ===========================================================================
def page_concepts():
    st.subheader("Concepts")
    c_search = st.text_input("Search concepts (label, domain, or ID):", key="c_search").strip().lower()
    concepts_all = cached_by_type(_version(), "concept")

    if c_search:
        hits = [c for c in concepts_all
                if c_search in c.get("label", "").lower()
                or c_search in c.get("domain", "").lower()
                or c_search in c.get("id", "").lower()]
        st.caption(f"{len(hits)} of {len(concepts_all)} concepts match.")
        for c in hits:
            c_id = c["id"]
            with st.expander(f"**{c.get('label', c_id)}** — {c.get('domain', '—')}"):
                st.caption(f"ID: `{c_id}`")
                if c.get("definition"):
                    st.markdown(c["definition"])
                with st.form(f"cedit_{c_id}"):
                    e_lbl = st.text_input("Label:", value=c.get("label", ""))
                    e_dom = st.text_input("Domain:", value=c.get("domain", ""))
                    e_def = st.text_area("Definition:", value=c.get("definition", ""))
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.form_submit_button("Save"):
                            kg.update_concept_metadata(c_id, label=e_lbl, domain=e_dom, definition=e_def)
                            st.success("Updated.")
                            _rerun_fresh()
                    with c2:
                        if st.form_submit_button("🗑️ Delete"):
                            kg.remove_node(c_id)
                            st.success("Deleted.")
                            _rerun_fresh()
        if not hits:
            st.info(f"No concepts matching '{c_search}'.")
    else:
        st.info(f"{len(concepts_all)} concepts in graph. Type a search to find and edit them.")

    if len(concepts_all) >= 2:
        st.markdown("---")
        with st.expander("🔗 Connect Rhizomatic Concepts"):
            with st.form("rhizome_form"):
                r_search_a = st.text_input("Find first concept:", key="rh_a").strip().lower()
                r_search_b = st.text_input("Find second concept:", key="rh_b").strip().lower()
                hits_a = [c for c in concepts_all
                          if r_search_a in c.get("label", "").lower() or r_search_a in c.get("id", "").lower()
                          ] if r_search_a else concepts_all[:50]
                hits_b = [c for c in concepts_all
                          if r_search_b in c.get("label", "").lower() or r_search_b in c.get("id", "").lower()
                          ] if r_search_b else concepts_all[:50]
                opts_a = {c["id"]: c.get("label", c["id"]) for c in hits_a}
                opts_b = {c["id"]: c.get("label", c["id"]) for c in hits_b}
                if opts_a and opts_b:
                    con_a = st.selectbox("First:", options=list(opts_a.keys()),
                                         format_func=lambda x: opts_a.get(x, x))
                    rel = st.selectbox("Relation:", ["critiques", "extends", "redefines",
                                                    "reappropriates", "related_to"])
                    con_b = st.selectbox("Second:", options=list(opts_b.keys()),
                                         format_func=lambda x: opts_b.get(x, x))
                    if st.form_submit_button("Connect", use_container_width=True):
                        if con_a != con_b:
                            kg.link_concepts_rhizomatic(con_a, con_b, rel)
                            st.success("Connected.")
                            _rerun_fresh()
                        else:
                            st.error("Cannot connect a concept to itself.")

    st.markdown("---")
    with st.expander("➕ New Concept"):
        with st.form("new_concept"):
            nc_id = st.text_input("Identifier (e.g. cyborg_theory):").strip()
            nc_lbl = st.text_input("Display Name:").strip()
            nc_dom = st.text_input("Domain:").strip()
            nc_def = st.text_area("Definition:").strip()
            if st.form_submit_button("Create", use_container_width=True):
                if nc_id and nc_lbl:
                    kg.add_concept_node(f"concept:{nc_id.lower()}", label=nc_lbl,
                                        domain=nc_dom, definition=nc_def)
                    st.success("Created.")
                    _rerun_fresh()


# ===========================================================================
# PAGE: Agents
# ===========================================================================
def page_agents():
    st.subheader("Agents")
    agents = cached_by_type(_version(), "agent")

    if not agents:
        st.info("No agents registered yet.")
        return

    st.caption(f"{len(agents)} agents total. Filter below to narrow before browsing.")
    afc1, afc2 = st.columns([3, 1])
    with afc1:
        agent_search = st.text_input(
            "Search name:", key="agent_search", placeholder="e.g. foucault, ahmed, maska",
        ).strip().lower()
    with afc2:
        role_choices = ["all"] + sorted({a.get("role", "?") for a in agents})
        agent_role_filter = st.selectbox("Role:", role_choices, key="agent_role_f")

    def _agent_matches(a):
        if agent_role_filter != "all" and a.get("role") != agent_role_filter:
            return False
        if agent_search and agent_search not in (a.get("name", "") or "").lower():
            return False
        return True

    agents_filtered = [a for a in agents if _agent_matches(a)]
    st.caption(f"**{len(agents_filtered)} match filters.**")

    PAGE_SIZE_A = 25
    total_a_pages = max(1, (len(agents_filtered) + PAGE_SIZE_A - 1) // PAGE_SIZE_A)
    a_page = st.number_input(
        f"Page (1 – {total_a_pages})", min_value=1, max_value=total_a_pages,
        value=1, step=1, key="agent_page",
    )
    a_page_start = (int(a_page) - 1) * PAGE_SIZE_A

    for a in agents_filtered[a_page_start: a_page_start + PAGE_SIZE_A]:
        a_id = a["id"]
        with st.expander(f"**{a.get('name', a_id)}** — {a.get('role', '—')}"):
            with st.form(f"aedit_{a_id}"):
                ea_name = st.text_input("Name:", value=a.get("name", ""))
                current_role = a.get("role", "author")
                roles_for_this = AGENT_ROLES if current_role in AGENT_ROLES else AGENT_ROLES + [current_role]
                ea_role = st.selectbox(
                    "Role:", roles_for_this, index=roles_for_this.index(current_role),
                )
                c1, c2 = st.columns(2)
                with c1:
                    if st.form_submit_button("Save"):
                        kg.update_agent_node(a_id, name=ea_name, role=ea_role)
                        st.success("Updated.")
                        _rerun_fresh()
                with c2:
                    if st.form_submit_button("🗑️ Delete"):
                        kg.remove_node(a_id)
                        st.success("Deleted.")
                        _rerun_fresh()

    st.markdown("---")
    with st.expander("➕ New Agent"):
        with st.form("new_agent"):
            na_id = st.text_input("Short ID (e.g. haraway):").strip()
            na_name = st.text_input("Full Name:").strip()
            na_role = st.selectbox("Role:", ["author", "translator"])
            if st.form_submit_button("Create", use_container_width=True):
                if na_id and na_name:
                    kg.add_agent_node(na_id, na_name, role=na_role)
                    st.success("Created.")
                    _rerun_fresh()


# ===========================================================================
# PAGE: Sources
# ===========================================================================
def page_sources():
    st.subheader("Source Texts")
    agent_opts = cached_agent_opts(_version())
    sources = cached_by_type(_version(), "source_text")

    if not sources:
        st.info("No source texts registered yet.")
        return

    st.caption(f"{len(sources)} sources total. Filter below to narrow before browsing.")
    fc1, fc2, fc3 = st.columns([2, 2, 1])
    with fc1:
        src_search = st.text_input(
            "Search title/author:", key="src_search",
            placeholder="e.g. foucault, life of art, maska",
        ).strip().lower()
    with fc2:
        ptypes = sorted({s.get("project_type", "_unset") or "_unset" for s in sources})
        src_ptype = st.selectbox("project_type:", ["all"] + ptypes, key="src_ptype")
    with fc3:
        no_author_only = st.checkbox(
            "Only no-author", key="src_noauth",
            help="Show only sources that have no written_by edge",
        )

    src_author = cached_src_author(_version())

    def _source_matches(s):
        if src_ptype != "all" and (s.get("project_type", "_unset") or "_unset") != src_ptype:
            return False
        if no_author_only and src_author.get(s["id"]):
            return False
        if src_search:
            title = (s.get("title", "") or "").lower()
            author_id = src_author.get(s["id"], "")
            author_name = agent_opts.get(author_id, "").lower()
            if src_search not in title and src_search not in author_name:
                return False
        return True

    filtered = [s for s in sources if _source_matches(s)]
    st.caption(f"**{len(filtered)} match filters.**")

    PAGE_SIZE = 15
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input(
        f"Page (1 – {total_pages})", min_value=1, max_value=total_pages,
        value=1, step=1, key="src_page",
    )
    page_start = (int(page) - 1) * PAGE_SIZE
    page_rows = filtered[page_start: page_start + PAGE_SIZE]

    for s in page_rows:
        s_id = s["id"]
        current_author_id = src_author.get(s_id, "_none")
        connected = []
        for _, tgt, data in kg.G.out_edges(s_id, data=True):
            rel = data.get("relation")
            if rel in ("written_by", "translated_by", "edited_by"):
                agent_name = kg.G.nodes[tgt].get("name", tgt) if kg.G.has_node(tgt) else tgt
                connected.append(f"{rel.replace('_', ' ')}: {agent_name}")
        connected_str = " | ".join(connected) if connected else "(no agent edges)"
        header = f"**{s.get('title', s_id)}** — {s.get('year', '—')} — {connected_str}"
        with st.expander(header):
            with st.form(f"sedit_{s_id}"):
                es_title = st.text_input("Title:", value=s.get("title", ""))
                es_year = st.number_input(
                    "Year:", min_value=1800, max_value=2030, value=s.get("year") or 2000,
                )
                author_options = ["_none"] + list(agent_opts.keys())
                try:
                    author_index = author_options.index(current_author_id)
                except ValueError:
                    author_index = 0
                es_auth = st.selectbox(
                    "Author:", options=author_options, index=author_index,
                    format_func=lambda x: ("None" if x == "_none" else agent_opts.get(x, x)),
                )
                c1, c2 = st.columns(2)
                with c1:
                    if st.form_submit_button("Save"):
                        auth_val = None if es_auth == "_none" else es_auth
                        kg.update_source_text_node(
                            s_id, title=es_title, year=es_year, author_id=auth_val,
                        )
                        st.success("Updated.")
                        _rerun_fresh()
                with c2:
                    if st.form_submit_button("🗑️ Delete"):
                        kg.remove_node(s_id)
                        st.success("Deleted.")
                        _rerun_fresh()

    st.markdown("---")
    with st.expander("➕ New Source Text"):
        with st.form("new_source"):
            ns_id = st.text_input("Short ID:").strip()
            ns_title = st.text_input("Title:").strip()
            ns_year = st.number_input("Year:", min_value=1800, max_value=2030, value=2000)
            ns_auth = st.selectbox("Author:", options=["_none"] + list(agent_opts.keys()),
                                   format_func=lambda x: (
                                       "None" if x == "_none" else agent_opts.get(x, x)))
            if st.form_submit_button("Create", use_container_width=True):
                if ns_id and ns_title:
                    auth_val = None if ns_auth == "_none" else ns_auth
                    kg.add_source_text_node(ns_id, title=ns_title, author_id=auth_val, year=ns_year)
                    st.success("Created.")
                    _rerun_fresh()


# ===========================================================================
# PAGE: Lineage Cleanup
# ===========================================================================
def page_lineages():
    st.subheader("Lineage Cleanup")
    st.write("Merge messy imported lineages into clean conceptual ones.")

    all_lineages = cached_lineages(_version())
    if not all_lineages:
        st.info("No lineages registered yet.")
        return

    st.caption(f"{len(all_lineages)} distinct lineages in graph.")
    with st.form("lineage_cleanup_form"):
        messy_selections = st.multiselect(
            "Select lineages to merge:", options=all_lineages,
            help="Select all raw lineages that belong to the same conceptual category",
        )
        clean_name = st.text_input("Merge into:", placeholder="e.g. Lacanian Psychoanalysis").strip()
        if st.form_submit_button("Unify Lineages", use_container_width=True):
            if messy_selections and clean_name:
                changes = kg.merge_lineages(messy_selections, clean_name)
                st.success(f"Unified {changes} mappings into '{clean_name}'.")
                _rerun_fresh()
            else:
                st.error("Select at least one lineage and provide a target name.")

    if glossary.entries:
        if st.button("🪄 Auto-Align to Glossary", use_container_width=True,
                     help="Match chaotic lineages against your manual glossary"):
            aligned = kg.bulk_align_lineages_with_glossary(glossary.entries)
            st.success(f"Auto-aligned {aligned} translations.")
            _rerun_fresh()


# ===========================================================================
# PAGE: Extraction Review
# ===========================================================================
def page_review():
    st.subheader("Entity Extraction Review")
    st.caption(
        "Review mid-confidence records produced by `run_entity_extraction.py`. "
        "Accept rows you want in the KG; remaining items stay in `data/extraction_review.json`."
    )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if PREVIEW_PATH.exists():
            st.metric("Preview generated", PREVIEW_PATH.stat().st_size // 1024, "KB")
        else:
            st.info("No preview yet — run `python run_entity_extraction.py --dry-run --preview-patterns`.")
    with col_b:
        if REVIEW_PATH.exists():
            st.metric("Review queue size", REVIEW_PATH.stat().st_size // 1024, "KB")
    with col_c:
        if st.button("🔄 Reload review queue"):
            st.rerun()

    if not REVIEW_PATH.exists():
        st.warning("No `data/extraction_review.json` found. Run the extractor first.")
        return

    try:
        review_records = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        st.error(f"Could not load review queue: {e}")
        review_records = []

    if not review_records:
        st.success("Review queue is empty — nothing pending.")
        return

    kinds_present = Counter(r["kind"] for r in review_records)
    kind_choices = sorted(kinds_present.keys())

    f_col1, f_col2, f_col3 = st.columns([1, 1, 2])
    with f_col1:
        kind_filter = st.selectbox(
            "Kind", ["all"] + kind_choices,
            format_func=lambda k: f"{k} ({kinds_present.get(k, len(review_records))})" if k != "all" else f"all ({len(review_records)})",
        )
    with f_col2:
        min_conf = st.slider("Min confidence", 0.0, 1.0, 0.55, 0.05)
    with f_col3:
        text_filter = st.text_input("Filter by text (author/title/name)").strip().lower()

    filtered = [
        r for r in review_records
        if (kind_filter == "all" or r["kind"] == kind_filter)
        and r.get("confidence", 0) >= min_conf
        and (not text_filter or _record_matches_text(r, text_filter))
    ]
    st.caption(f"**{len(filtered)} of {len(review_records)}** records match filters.")

    bulk_col_a, bulk_col_b = st.columns(2)
    with bulk_col_a:
        with st.expander("🚀 Bulk accept (use with care)", expanded=False):
            bulk_kind = st.selectbox(
                "Accept all records of kind:", ["(pick a kind)"] + kind_choices, key="bulk_kind",
            )
            bulk_min_conf = st.slider(
                "Minimum confidence for bulk accept:", 0.55, 1.0, 0.7, 0.05, key="bulk_min_conf",
            )
            if bulk_kind != "(pick a kind)" and st.button(
                f"Accept all {bulk_kind} ≥ {bulk_min_conf}", type="primary",
            ):
                accepted = 0
                remaining = []
                for r in review_records:
                    if r["kind"] == bulk_kind and r.get("confidence", 0) >= bulk_min_conf:
                        _commit_record(kg, r)
                        accepted += 1
                    else:
                        remaining.append(r)
                REVIEW_PATH.write_text(json.dumps(remaining, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                kg.save()
                st.success(f"Committed {accepted} records. KG saved.")
                _rerun_fresh()

    with bulk_col_b:
        with st.expander("🗑️ Bulk reject (remove all matching the current filter)", expanded=False):
            st.caption(
                f"This will permanently remove **all {len(filtered)} records currently matching the filter** "
                "from the review queue. They will NOT be committed to the KG."
            )
            confirm = st.text_input(
                "Type the count to confirm:", placeholder=str(len(filtered)), key="bulk_reject_confirm",
            )
            if st.button("🗑️ Discard filtered records", type="secondary"):
                if confirm == str(len(filtered)) and filtered:
                    filtered_ids = {id(r) for r in filtered}
                    remaining = [r for r in review_records if id(r) not in filtered_ids]
                    REVIEW_PATH.write_text(
                        json.dumps(remaining, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
                    )
                    st.success(f"Discarded {len(filtered)} records.")
                    st.rerun()
                else:
                    st.error("Confirmation count mismatch — type the exact filtered count to proceed.")

    PAGE_SIZE = 10
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input(
        f"Page (1 – {total_pages})", min_value=1, max_value=total_pages, value=1, step=1,
    )
    page_start = (page - 1) * PAGE_SIZE
    page_recs = filtered[page_start: page_start + PAGE_SIZE]

    for r in page_recs:
        rid = r.get("payload", {}).get("cited_id") \
            or r.get("payload", {}).get("work_id") \
            or r.get("payload", {}).get("dedup_group") \
            or r.get("payload", {}).get("name") \
            or id(r)
        label = _record_label(r)
        with st.expander(f"`{r['kind']}` — {label}  (conf {r.get('confidence', 0):.2f})"):
            _render_record(r)
            st.markdown("---")
            label_to_target = {v: k for k, v in RECLASS_TARGETS.items()}
            options = [f"(keep as) {r['kind']}"] + list(RECLASS_TARGETS.values())
            choice = st.selectbox(
                "Commit as:", options, key=f"ca_{rid}_{page}",
                help="Keep the original kind, or reclassify this record into a "
                     "different entity type before committing (e.g. an 'author' "
                     "that is really a work title, or a 'work' that is a concept).",
            )
            if choice.startswith("(keep as)"):
                st.caption("Commits using the record's original fields.")
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ Accept → KG", key=f"acc_{rid}_{page}"):
                        _commit_record(kg, r)
                        kg.save()
                        _drop_from_queue(review_records, r)
                        st.success("Committed.")
                        _rerun_fresh()
                with cc2:
                    if st.button("🗑️ Reject", key=f"rej_{rid}_{page}"):
                        _drop_from_queue(review_records, r)
                        st.info("Removed from queue.")
                        st.rerun()
            else:
                target = label_to_target[choice]
                cands = _candidate_texts(r)
                with st.form(f"recl_{rid}_{page}"):
                    st.caption(f"Reclassify → **{choice}**. Confirm the fields:")
                    fields = _reclass_inputs(target, cands, f"rf_{rid}_{page}")
                    submitted = st.form_submit_button(f"✅ Commit as {choice}")
                if submitted:
                    err, _ = _commit_as(kg, target, fields)
                    if err:
                        st.error(err)
                    else:
                        kg.save()
                        _drop_from_queue(review_records, r)
                        st.success(f"Committed as {choice}.")
                        _rerun_fresh()
                if st.button("🗑️ Reject", key=f"rej2_{rid}_{page}"):
                    _drop_from_queue(review_records, r)
                    st.info("Removed from queue.")
                    st.rerun()


# ===========================================================================
# PAGE: KG Review (dubious LIVE nodes — edit / reclassify / delete / keep)
# ===========================================================================
def page_kg_review():
    st.subheader("KG Review — dubious live entries")
    st.caption(
        "Nodes already in the graph that look wrong. Unlike Extraction Review "
        "(candidates to add), these are fixed in place: edit, reclassify, delete, "
        "or keep. Run a scan to (re)build the list."
    )

    cc1, cc2 = st.columns([1, 3])
    with cc1:
        if st.button("🔎 Scan / rescan KG", use_container_width=True):
            data = {"nodes": [d for _, d in kg.G.nodes(data=True)],
                    "edges": [{"source": u, "target": v, **d}
                              for u, v, d in kg.G.edges(data=True)]}
            try:
                dismissed = set(json.loads(KG_DISMISSED_PATH.read_text(encoding="utf-8"))) \
                    if KG_DISMISSED_PATH.exists() else set()
            except Exception:
                dismissed = set()
            items = flag_dubious(data["nodes"], data["edges"], dismissed)
            KG_REVIEW_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            st.success(f"Flagged {len(items)} dubious nodes.")
            st.rerun()

    items = _load_kg_review()
    if not items:
        st.info("No review list yet (or empty). Press **Scan / rescan KG**.")
        return

    reasons = Counter(it.get("reason") for it in items)
    reason_choice = st.selectbox(
        "Reason:", ["all"] + sorted(reasons),
        format_func=lambda r: f"{r} ({reasons.get(r, len(items))})" if r != "all" else f"all ({len(items)})",
    )
    shown = [it for it in items if reason_choice == "all" or it.get("reason") == reason_choice]

    PAGE = 15
    pages = max(1, (len(shown) + PAGE - 1) // PAGE)
    pg_num = st.number_input(f"Page (1 – {pages})", min_value=1, max_value=pages, value=1, step=1)
    start = (int(pg_num) - 1) * PAGE

    edit_ptypes = list(dict.fromkeys(
        ["book_translation", "article_translation", "festival_programme",
         "exhibition_catalogue", "cited_container"] + RECLASS_PROJECT_TYPES))

    for it in shown[start: start + PAGE]:
        nid = it["id"]
        ntype = it.get("type")
        if not kg.G.has_node(nid):
            continue  # already removed elsewhere
        label = it.get("name") or it.get("title_en") or it.get("title") or it.get("title_sl") or nid
        # widget keys must be unique even when one node is flagged under >1 reason
        uk = f"{nid}::{it.get('reason')}"
        with st.expander(f"`{it.get('reason')}` — {ntype}: {label[:70]}"):
            st.caption(f"id: `{nid}`")
            if ntype == "source_text":
                st.markdown(
                    f"**title:** `{it.get('title')}`  ·  **EN:** `{it.get('title_en')}`  ·  "
                    f"**SL:** `{it.get('title_sl')}`  ·  **year:** `{it.get('year')}`  ·  "
                    f"**type:** `{it.get('project_type')}`"
                )
            elif ntype == "agent":
                st.markdown(f"**name:** `{it.get('name')}`  ·  **role:** `{it.get('role')}`")

            action = st.selectbox(
                "Action:", ["Edit fields", "Reclassify type", "Delete node", "Keep (dismiss)"],
                key=f"act_{uk}",
            )

            if action == "Edit fields":
                with st.form(f"kedit_{uk}"):
                    if ntype == "source_text":
                        e_ten = st.text_input("Title (EN):", value=it.get("title_en") or "", key=f"e_ten_{uk}")
                        e_tsl = st.text_input("Title (SL):", value=it.get("title_sl") or "", key=f"e_tsl_{uk}")
                        e_title = st.text_input("Display title:", value=it.get("title") or "", key=f"e_ti_{uk}")
                        e_yr = st.text_input("Year:", value=str(it.get("year") or ""), key=f"e_yr_{uk}")
                        cur_pt = it.get("project_type") or "cited_work"
                        pt_opts = list(dict.fromkeys([cur_pt] + edit_ptypes))
                        e_pt = st.selectbox("Project type:", pt_opts, index=0, key=f"e_pt_{uk}")
                        if st.form_submit_button("💾 Save"):
                            try:
                                yr = int(e_yr) if e_yr.strip() else None
                            except ValueError:
                                yr = None
                            kg.update_source_text_node(
                                nid, title=e_title or None, year=yr, project_type=e_pt,
                                title_en=e_ten.strip() or None, title_sl=e_tsl.strip() or None,
                            )
                            kg.save()
                            _drop_kg_review(items, it)
                            st.success("Saved.")
                            _rerun_fresh()
                    elif ntype == "agent":
                        e_name = st.text_input("Name:", value=it.get("name") or "", key=f"e_nm_{uk}")
                        cur_role = it.get("role") or "author"
                        role_opts = list(dict.fromkeys([cur_role] + RECLASS_AGENT_ROLES))
                        e_role = st.selectbox("Role:", role_opts, index=0, key=f"e_rl_{uk}")
                        if st.form_submit_button("💾 Save"):
                            kg.update_agent_node(nid, name=e_name or None, role=e_role)
                            kg.save()
                            _drop_kg_review(items, it)
                            st.success("Saved.")
                            _rerun_fresh()
                    else:
                        st.caption("No inline editor for this type — use Reclassify or Delete.")
                        st.form_submit_button("💾 Save", disabled=True)

            elif action == "Reclassify type":
                label_to_target = {v: k for k, v in RECLASS_TARGETS.items()}
                choice = st.selectbox("New type:", list(RECLASS_TARGETS.values()), key=f"rt_{uk}")
                target = label_to_target[choice]
                cands = {
                    "primary": label, "name": it.get("name") or label,
                    "title_en": it.get("title_en") or "", "title_sl": it.get("title_sl") or "",
                    "author": "", "year": it.get("year"), "city": "",
                }
                with st.form(f"krecl_{uk}"):
                    st.caption(f"Reclassify → **{choice}** (old node's edges are migrated, then it is removed).")
                    fields = _reclass_inputs(target, cands, f"krf_{uk}")
                    if st.form_submit_button(f"🔀 Reclassify as {choice}"):
                        err = _reclassify_live_node(kg, nid, target, fields)
                        if err:
                            st.error(err)
                        else:
                            kg.save()
                            _drop_kg_review(items, it)
                            st.success(f"Reclassified as {choice}.")
                            _rerun_fresh()

            elif action == "Delete node":
                st.warning("Removes the node and its edges from the KG.")
                if st.button("🗑️ Confirm delete", key=f"kdel_{uk}"):
                    kg.remove_node(nid)
                    kg.save()
                    _drop_kg_review(items, it)
                    st.success("Deleted.")
                    _rerun_fresh()

            else:  # Keep (dismiss)
                if st.button("✅ Keep — it's fine", key=f"kkeep_{uk}"):
                    _drop_kg_review(items, it, dismiss=True)
                    st.info("Kept; won't be flagged again.")
                    st.rerun()


# ===========================================================================
# Entrypoint: sidebar (common frame) + navigation
# ===========================================================================
st.set_page_config(page_title="Knowledge Graph Workspace", layout="wide")

st.sidebar.header("📂 Database Control")
if st.sidebar.button("💾 Hard Save to Disk", use_container_width=True):
    kg.save()
    st.sidebar.success("Database saved.")

st.sidebar.markdown("---")
st.sidebar.subheader("📈 Statistics")
stats = cached_stats(_version())
for label, key in [
    ("Nodes", "nodes_total"), ("Edges", "edges_total"),
    ("Terms", "node_term"), ("Mappings", "node_translation_mapping"),
    ("Concepts", "node_concept"), ("Agents", "node_agent"),
    ("Sources", "node_source_text"),
]:
    st.sidebar.markdown(f"• {label}: `{stats.get(key, 0)}`")

pg = st.navigation([
    st.Page(page_terms, title="Terms & Mappings", icon=":material/search:", default=True),
    st.Page(page_concepts, title="Concepts", icon=":material/lightbulb:"),
    st.Page(page_agents, title="Agents", icon=":material/person:"),
    st.Page(page_sources, title="Sources", icon=":material/menu_book:"),
    st.Page(page_lineages, title="Lineages", icon=":material/cleaning_services:"),
    st.Page(page_review, title="Extraction Review", icon=":material/inbox:"),
    st.Page(page_kg_review, title="KG Review", icon=":material/rule:"),
])
pg.run()
