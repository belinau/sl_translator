# kg_editor_ui.py
#
# Knowledge Graph Curation Workspace
# Search-first design: search → select → edit. Never renders all entities at once.
#

import importlib.util
import sys
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

st.set_page_config(page_title="Knowledge Graph Workspace", layout="wide")


@st.cache_resource
def load_kg():
    return KnowledgeGraph()


@st.cache_resource
def load_glossary():
    return Glossary()


kg = load_kg()
glossary = load_glossary()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("📂 Database Control")
if st.sidebar.button("💾 Hard Save to Disk", use_container_width=True):
    kg.save()
    st.sidebar.success("Database saved.")

st.sidebar.markdown("---")
st.sidebar.subheader("📈 Statistics")
stats = kg.stats()
for label, key in [
    ("Nodes", "nodes_total"), ("Edges", "edges_total"),
    ("Terms", "node_term"), ("Mappings", "node_translation_mapping"),
    ("Concepts", "node_concept"), ("Agents", "node_agent"),
    ("Sources", "node_source_text"),
]:
    st.sidebar.markdown(f"• {label}: `{stats.get(key, 0)}`")

# ---------------------------------------------------------------------------
# Entity tab selection
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Helpers used by the Extraction Review tab (defined before the tab block
# because Streamlit re-runs the script top-to-bottom on each interaction).
# ---------------------------------------------------------------------------
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


tab_search, tab_concepts, tab_agents, tab_sources, tab_lineages, tab_review = st.tabs([
    "🔍 Terms & Mappings", "💡 Concepts", "👤 Agents", "📖 Sources", "🧹 Lineages",
    "📥 Extraction Review",
])

# ===========================================================================
# TAB: Terms & Mappings (search-first)
# ===========================================================================
with tab_search:
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

                    # Edit term properties
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
                            st.rerun()

                    # Delete term
                    if st.button("🗑️ Delete this term", key=f"del_{term_id}"):
                        kg.remove_node(term_id)
                        kg._rebuild_indices()
                        st.success("Deleted.")
                        st.rerun()

                    # Variants
                    variants = match.get("variants", [])
                    if variants:
                        st.markdown(f"**Variants:** {', '.join(variants)}")

                    # Translations / mappings
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
                                            st.rerun()
                                    with c2:
                                        if st.form_submit_button("🗑️ Delete"):
                                            kg.remove_node(mapping_id)
                                            st.success("Deleted.")
                                            st.rerun()
                            else:
                                st.caption("_Legacy edge (no mapping node) — not editable._")
    else:
        st.info("Type a search query above to find and edit terms and their mappings.")

    # Quick-add mapping (always visible)
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
                    st.rerun()
                else:
                    st.error("Both source and target terms required.")

    # Add variant
    with st.expander("🏷️ Add Variant to Term"):
        var_search = st.text_input("Find term:", key="var_search").strip().lower()
        if var_search:
            all_terms = kg.get_all_by_type("term")
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
                            st.rerun()
            else:
                st.info(f"No terms matching '{var_search}'.")

# ===========================================================================
# TAB: Concepts
# ===========================================================================
with tab_concepts:
    st.subheader("Concepts")

    # Search existing
    c_search = st.text_input("Search concepts (label, domain, or ID):", key="c_search").strip().lower()
    concepts_all = kg.get_all_by_type("concept")

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
                            st.rerun()
                    with c2:
                        if st.form_submit_button("🗑️ Delete"):
                            kg.remove_node(c_id)
                            st.success("Deleted.")
                            st.rerun()
        if not hits:
            st.info(f"No concepts matching '{c_search}'.")
    else:
        st.info(f"{len(concepts_all)} concepts in graph. Type a search to find and edit them.")

    # Rhizomatic linking
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
                            st.rerun()
                        else:
                            st.error("Cannot connect a concept to itself.")

    # New concept
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
                    st.rerun()

# ===========================================================================
# TAB: Agents
# ===========================================================================
with tab_agents:
    st.subheader("Agents")
    agents = kg.get_all_by_type("agent")
    AGENT_ROLES = ["author", "translator", "editor", "interviewer", "curator", "organization"]

    if not agents:
        st.info("No agents registered yet.")
    else:
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
                    if current_role not in AGENT_ROLES:
                        AGENT_ROLES_FOR_THIS = AGENT_ROLES + [current_role]
                    else:
                        AGENT_ROLES_FOR_THIS = AGENT_ROLES
                    ea_role = st.selectbox(
                        "Role:", AGENT_ROLES_FOR_THIS,
                        index=AGENT_ROLES_FOR_THIS.index(current_role),
                    )
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.form_submit_button("Save"):
                            kg.update_agent_node(a_id, name=ea_name, role=ea_role)
                            st.success("Updated.")
                            st.rerun()
                    with c2:
                        if st.form_submit_button("🗑️ Delete"):
                            kg.remove_node(a_id)
                            st.success("Deleted.")
                            st.rerun()

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
                    st.rerun()

# ===========================================================================
# TAB: Sources
# ===========================================================================
with tab_sources:
    st.subheader("Source Texts")
    agents_for_select = kg.get_all_by_type("agent")
    agent_opts = {a["id"]: a.get("name", a["id"]) for a in agents_for_select}
    sources = kg.get_all_by_type("source_text")

    if not sources:
        st.info("No source texts registered yet.")
    else:
        # --- Filter / search controls (no rendering of 800+ forms upfront) ---
        st.caption(f"{len(sources)} sources total. Filter below to narrow before browsing.")
        fc1, fc2, fc3 = st.columns([2, 2, 1])
        with fc1:
            src_search = st.text_input(
                "Search title/author:", key="src_search",
                placeholder="e.g. foucault, life of art, maska",
            ).strip().lower()
        with fc2:
            # Build a project_type set from data
            ptypes = sorted({s.get("project_type", "_unset") or "_unset" for s in sources})
            src_ptype = st.selectbox(
                "project_type:", ["all"] + ptypes, key="src_ptype",
            )
        with fc3:
            no_author_only = st.checkbox(
                "Only no-author", key="src_noauth",
                help="Show only sources that have no written_by edge",
            )

        # Pre-index author lookup per source so filtering by text and no-author is fast.
        # This is a single pass over edges (cheap, ~K thousand edges total).
        src_author: dict = {}
        for u, v, d in kg.G.edges(data=True):
            if d.get("relation") == "written_by":
                # First written_by edge per source wins
                src_author.setdefault(u, v)

        def _source_matches(s):
            if src_ptype != "all" and (s.get("project_type", "_unset") or "_unset") != src_ptype:
                return False
            if no_author_only and src_author.get(s["id"]):
                return False
            if src_search:
                title = (s.get("title", "") or "").lower()
                author_id = src_author.get(s["id"], "")
                author_name = (kg.G.nodes[author_id].get("name", "") if author_id and kg.G.has_node(author_id) else "").lower()
                if src_search not in title and src_search not in author_name:
                    return False
            return True

        filtered = [s for s in sources if _source_matches(s)]
        st.caption(f"**{len(filtered)} match filters.**")

        # --- Pagination: only render forms for the current page ---
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
            # Brief header listing connected agents
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
                        "Year:", min_value=1800, max_value=2030,
                        value=s.get("year") or 2000,
                    )
                    author_options = ["_none"] + list(agent_opts.keys())
                    try:
                        author_index = author_options.index(current_author_id)
                    except ValueError:
                        author_index = 0
                    es_auth = st.selectbox(
                        "Author:", options=author_options,
                        index=author_index,
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
                            st.rerun()
                    with c2:
                        if st.form_submit_button("🗑️ Delete"):
                            kg.remove_node(s_id)
                            st.success("Deleted.")
                            st.rerun()

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
                    st.rerun()

# ===========================================================================
# TAB: Lineage Cleanup
# ===========================================================================
with tab_lineages:
    st.subheader("Lineage Cleanup")
    st.write("Merge messy imported lineages into clean conceptual ones.")

    all_lineages = kg.get_all_lineages()
    if all_lineages:
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
                    st.rerun()
                else:
                    st.error("Select at least one lineage and provide a target name.")

        if glossary.entries:
            if st.button("🪄 Auto-Align to Glossary", use_container_width=True,
                         help="Match chaotic lineages against your manual glossary"):
                aligned = kg.bulk_align_lineages_with_glossary(glossary.entries)
                st.success(f"Auto-aligned {aligned} translations.")
                st.rerun()
    else:
        st.info("No lineages registered yet.")


# ===========================================================================
# TAB: Extraction Review (mid-confidence records from entity extractor)
# ===========================================================================
with tab_review:
    st.subheader("Entity Extraction Review")
    st.caption(
        "Review mid-confidence records produced by `run_entity_extraction.py`. "
        "Accept rows you want in the KG; remaining items stay in `data/extraction_review.json`."
    )

    import json
    from collections import Counter

    REVIEW_PATH = Path("data/extraction_review.json")
    PREVIEW_PATH = Path("data/extraction_pattern_preview.md")

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
    else:
        try:
            review_records = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            st.error(f"Could not load review queue: {e}")
            review_records = []

        if not review_records:
            st.success("Review queue is empty — nothing pending.")
        else:
            # Filter controls
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

            # Bulk operations
            bulk_col_a, bulk_col_b = st.columns(2)
            with bulk_col_a:
                with st.expander("🚀 Bulk accept (use with care)", expanded=False):
                    bulk_kind = st.selectbox(
                        "Accept all records of kind:",
                        ["(pick a kind)"] + kind_choices,
                        key="bulk_kind",
                    )
                    bulk_min_conf = st.slider(
                        "Minimum confidence for bulk accept:", 0.55, 1.0, 0.7, 0.05,
                        key="bulk_min_conf",
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
                        st.rerun()

            with bulk_col_b:
                with st.expander("🗑️ Bulk reject (remove all matching the current filter)", expanded=False):
                    st.caption(
                        f"This will permanently remove **all {len(filtered)} records currently matching the filter** "
                        "from the review queue. They will NOT be committed to the KG."
                    )
                    confirm = st.text_input(
                        "Type the count to confirm:",
                        placeholder=str(len(filtered)),
                        key="bulk_reject_confirm",
                    )
                    if st.button("🗑️ Discard filtered records", type="secondary"):
                        if confirm == str(len(filtered)) and filtered:
                            filtered_ids = {id(r) for r in filtered}
                            remaining = [r for r in review_records if id(r) not in filtered_ids]
                            REVIEW_PATH.write_text(
                                json.dumps(remaining, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8",
                            )
                            st.success(f"Discarded {len(filtered)} records.")
                            st.rerun()
                        else:
                            st.error("Confirmation count mismatch — type the exact filtered count to proceed.")

            # Pagination (smaller default page so rendering stays snappy)
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
                    rcol1, rcol2 = st.columns(2)
                    with rcol1:
                        if st.button("✅ Accept → KG", key=f"acc_{rid}_{page}"):
                            _commit_record(kg, r)
                            kg.save()
                            # Remove from review file
                            remaining = [x for x in review_records if x is not r]
                            REVIEW_PATH.write_text(json.dumps(remaining, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                            st.success("Committed.")
                            st.rerun()
                    with rcol2:
                        if st.button("🗑️ Reject (remove from queue)", key=f"rej_{rid}_{page}"):
                            remaining = [x for x in review_records if x is not r]
                            REVIEW_PATH.write_text(json.dumps(remaining, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                            st.info("Removed from queue.")
                            st.rerun()


