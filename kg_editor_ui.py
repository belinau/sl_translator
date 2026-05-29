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
tab_search, tab_concepts, tab_agents, tab_sources, tab_lineages = st.tabs([
    "🔍 Terms & Mappings", "💡 Concepts", "👤 Agents", "📖 Sources", "🧹 Lineages",
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

    if agents:
        for a in agents:
            a_id = a["id"]
            with st.expander(f"**{a.get('name', a_id)}** — {a.get('role', '—')}"):
                with st.form(f"aedit_{a_id}"):
                    ea_name = st.text_input("Name:", value=a.get("name", ""))
                    ea_role = st.selectbox("Role:", ["author", "translator"],
                                           index=["author", "translator"].index(a.get("role", "author")))
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
    else:
        st.info("No agents registered yet.")

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

    if sources:
        for s in sources:
            s_id = s["id"]
            with st.expander(f"**{s.get('title', s_id)}** — {s.get('year', '—')}"):
                with st.form(f"sedit_{s_id}"):
                    es_title = st.text_input("Title:", value=s.get("title", ""))
                    es_year = st.number_input("Year:", min_value=1800, max_value=2030,
                                              value=s.get("year") or 2000)
                    es_auth = st.selectbox("Author:", options=["_none"] + list(agent_opts.keys()),
                                           format_func=lambda x: (
                                               "None" if x == "_none" else agent_opts.get(x, x)))
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.form_submit_button("Save"):
                            auth_val = None if es_auth == "_none" else es_auth
                            kg.update_source_text_node(s_id, title=es_title,
                                                        year=es_year, author_id=auth_val)
                            st.success("Updated.")
                            st.rerun()
                    with c2:
                        if st.form_submit_button("🗑️ Delete"):
                            kg.remove_node(s_id)
                            st.success("Deleted.")
                            st.rerun()
    else:
        st.info("No source texts registered yet.")

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