# kg_editor_ui.py
#
# High-Efficiency, Clean Workspace for Knowledge Graph Curation
# Full CRUD for: terms, mappings, concepts, agents, sources, lineages
#

import importlib.util
import sys
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Direct module imports — bypass translate_core/__init__.py which pulls in
# heavy dependencies (doc_parser → markitdown, llm → mlx_lm) that the editor
# doesn't need and may not be installed in the Streamlit environment.
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, str(_BASE / path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Config first (no heavy deps)
_config = _load_module("config", "config.py")

# Then the two modules the editor actually uses
_kg_mod = _load_module("translate_core.knowledge_graph", "translate_core/knowledge_graph.py")
_gl_mod = _load_module("translate_core.glossary", "translate_core/glossary.py")

KnowledgeGraph = _kg_mod.KnowledgeGraph
Glossary = _gl_mod.Glossary

# Page configuration for dense, wide layout
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
# Sidebar: Global Actions & Metrics
# ---------------------------------------------------------------------------
st.sidebar.header("📂 Database Control")
if st.sidebar.button("💾 Hard Save to Disk", use_container_width=True):
    kg.save()
    st.sidebar.success("Database saved.")

st.sidebar.markdown("---")
st.sidebar.subheader("📈 Statistics")
stats = kg.stats()
st.sidebar.markdown(f"**Total Nodes:** `{stats.get('nodes_total', 0)}`")
st.sidebar.markdown(f"**Total Edges:** `{stats.get('edges_total', 0)}`")
st.sidebar.markdown(f"• Terms: `{stats.get('node_term', 0)}`")
st.sidebar.markdown(f"• Mappings: `{stats.get('node_translation_mapping', 0)}`")
st.sidebar.markdown(f"• Concepts: `{stats.get('node_concept', 0)}`")
st.sidebar.markdown(f"• Agents: `{stats.get('node_agent', 0)}`")
st.sidebar.markdown(f"• Sources: `{stats.get('node_source_text', 0)}`")

# ---------------------------------------------------------------------------
# Data Preparations (Safely handle empty states for widgets)
# ---------------------------------------------------------------------------
agents = kg.get_all_by_type("agent")
agent_options = {a["id"]: a.get("name", a["id"]) for a in agents}
agent_ids = list(agent_options.keys()) if agents else []
if not agent_options:
    agent_options = {"_none": "(No agents registered yet)"}
    agent_ids = ["_none"]

sources = kg.get_all_by_type("source_text")
source_options = {s["id"]: s.get("title", s["id"]) for s in sources}
source_ids = list(source_options.keys()) if sources else []
if not source_options:
    source_options = {"_none": "(No source texts registered yet)"}
    source_ids = ["_none"]

concepts = kg.get_all_by_type("concept")
concept_options = {c["id"]: c.get("label", c["id"]) for c in concepts}
concept_ids = list(concept_options.keys()) if concepts else []
if not concept_options:
    concept_options = {"_none": "(No concepts registered yet)"}
    concept_ids = ["_none"]

# ---------------------------------------------------------------------------
# Main Layout: Two Columns
# ---------------------------------------------------------------------------
workspace_col, utility_col = st.columns([1.0, 1.0], gap="large")

# ===========================================================================
# WORKSPACE COLUMN (Left: Search & Inline Edits)
# ===========================================================================
with workspace_col:
    st.subheader("🔍 Search & Active Curation")

    query = st.text_input(
        "Type an English or Slovenian prefix to retrieve translations:", ""
    ).strip()

    if query:
        matches = kg.extract_entities(query, target_lang="sl")
        if not matches:
            st.warning(f"No terms matching '{query}' found.")
        else:
            for match in matches:
                term_id = match.get("id")
                st.markdown(
                    f"### Term: `{match.get('term')}` (`{match.get('lang', '?').upper()}`)"
                )
                st.caption(
                    f"Node ID: {term_id} | Freq: {match.get('frequency', 0)} | "
                    f"Animate: {match.get('is_animate', False)}"
                )

                # Inline delete term button
                if st.button("🗑️ Delete Term", key=f"del_{term_id}"):
                    kg.remove_node(term_id)
                    kg._rebuild_indices()
                    st.success("Term deleted.")
                    st.rerun()

                translations = match.get("translations", [])
                st.markdown("**Translations & Inline Lineage Editing:**")
                if not translations:
                    st.info("No contextual translations mapped to this term.")
                else:
                    for idx, t in enumerate(translations):
                        mapping_id = t.get("mapping_id")
                        lineage = t.get("lineage", "general")

                        st.markdown(
                            f"👉 **`{t.get('term')}`** (Lineage: *{lineage}* | "
                            f"Confidence: `{t.get('confidence', 0.5):.2f}`"
                        )
                        if t.get("sources") or t.get("agents"):
                            st.caption(
                                f"└ *Context:* {', '.join(t.get('sources', []) + t.get('agents', []))}"
                            )

                        if mapping_id and kg.G.has_node(mapping_id):
                            with st.form(f"inline_edit_{mapping_id}_{idx}"):
                                st.write(
                                    f"Refine mapping: `{match.get('term')}` ➔ `{t.get('term')}`"
                                )

                                edit_lin = st.text_input(
                                    "Theoretical Lineage:", value=lineage
                                )
                                edit_reg = st.selectbox(
                                    "Style Register:",
                                    ["academic", "manifesto", "poetic", "colloquial"],
                                    index=[
                                        "academic",
                                        "manifesto",
                                        "poetic",
                                        "colloquial",
                                    ].index(t.get("register", "academic")),
                                )
                                edit_gloss = st.text_input(
                                    "Note / Gloss:", value=t.get("gloss", "")
                                )
                                edit_conf = st.slider(
                                    "Confidence Weight:",
                                    0.0,
                                    1.0,
                                    float(t.get("confidence", 0.5)),
                                    step=0.05,
                                )

                                if st.form_submit_button(
                                    "Update Mapping", use_container_width=True
                                ):
                                    kg.update_translation_mapping(
                                        mapping_id,
                                        lineage=edit_lin,
                                        register=edit_reg,
                                        gloss=edit_gloss,
                                        confidence=edit_conf,
                                    )
                                    st.success("Mapping updated.")
                                    st.rerun()

                                if st.form_submit_button(
                                    "🗑️ Delete Mapping Link", use_container_width=True
                                ):
                                    kg.remove_node(mapping_id)
                                    st.success("Mapping deleted.")
                                    st.rerun()
                        else:
                            st.caption(
                                "_Legacy edge (no mapping node) — cannot edit inline._"
                            )
                        st.markdown("---")
    else:
        # Default view: Quick Link Form
        st.subheader("⚡ Quick-Add Translation Mapping")
        st.write("Link two terms instantly with context metadata.")

        with st.form("quick_link_form"):
            new_src = st.text_input(
                "Source English Term (Lemma):", placeholder="e.g. gaze"
            ).strip()
            new_tgt = st.text_input(
                "Target Slovenian Term (Lemma/Phrase):", placeholder="e.g. pogled"
            ).strip()
            new_lin = st.text_input(
                "Theoretical Lineage:", placeholder="e.g. Mulveyan / Butlerian"
            ).strip()
            new_gloss = st.text_input(
                "Translator's Note (Optional):",
                placeholder="Explanation of lexical choices",
            ).strip()

            new_source = st.selectbox(
                "Associate with Source Text:",
                options=["_none"] + source_ids,
                format_func=lambda x: (
                    "No source reference" if x == "_none" else source_options.get(x, x)
                ),
            )
            new_agent = st.selectbox(
                "Attribute to Translator/Author:",
                options=["_none"] + agent_ids,
                format_func=lambda x: (
                    "No attribution" if x == "_none" else agent_options.get(x, x)
                ),
            )

            if st.form_submit_button(
                "⚡ Establish Contextual Translation Link", use_container_width=True
            ):
                if new_src and new_tgt:
                    cid = kg.add_concept_node(
                        f"concept:{new_src.replace(' ', '_').lower()}",
                        label=new_src,
                        domain="General",
                    )
                    sid = kg.add_term_node(
                        new_src, "en", concept_id=cid, is_phrase=True
                    )
                    tid = kg.add_term_node(new_tgt, "sl", is_phrase=True)

                    kg.link_translations_with_context(
                        src_term_id=sid,
                        tgt_term_id=tid,
                        confidence=1.0,
                        lineage=new_lin or "general",
                        gloss=new_gloss,
                        source_text_id=new_source if new_source != "_none" else None,
                        agent_id=new_agent if new_agent != "_none" else None,
                        verified=True,
                    )
                    st.success(
                        f"Contextual mapping created: '{new_src}' ➔ '{new_tgt}'."
                    )
                    st.rerun()
                else:
                    st.error("Both English and Slovenian terms are required.")

        # Add variant to existing term
        st.markdown("---")
        st.subheader("🏷️ Add Variant to Term")
        all_terms = [d for d in kg.get_all_by_type("term") if d.get("lang") == "en"]
        if all_terms:
            term_choices = {d["id"]: f"{d.get('term')} ({d.get('lang')})" for d in all_terms[:500]}
            with st.form("add_variant_form"):
                var_term = st.selectbox("Term:", options=list(term_choices.keys()),
                                        format_func=lambda x: term_choices.get(x, x))
                var_text = st.text_input("Variant form:", placeholder="e.g. gazes").strip()
                if st.form_submit_button("Add Variant", use_container_width=True):
                    if var_term and var_text:
                        kg.add_variant(var_term, var_text)
                        st.success(f"Variant '{var_text}' added.")
                        st.rerun()

# ===========================================================================
# UTILITY COLUMN (Right: Entity Management)
# ===========================================================================
with utility_col:
    st.subheader("🌿 Entities & Metadata")

    # ------------------------------------------------------------------
    # 1. Concept Management
    # ------------------------------------------------------------------
    with st.expander("💡 Concepts", expanded=True):
        # List existing
        if concepts:
            st.markdown("**Existing Concepts:**")
            for c in concepts[:50]:
                c_id = c["id"]
                with st.container():
                    st.markdown(f"- **{c.get('label', c_id)}** — Domain: *{c.get('domain', '—')}*")
                    if c.get("definition"):
                        st.caption(f"  _{c['definition'][:120]}_")
                    col_a, col_b = st.columns([1, 1])
                    with col_a:
                        if st.button("✏️ Edit", key=f"cedit_{c_id}"):
                            st.session_state[f"editing_concept"] = c_id
                    with col_b:
                        if st.button("🗑️", key=f"cdel_{c_id}"):
                            kg.remove_node(c_id)
                            st.success("Concept deleted.")
                            st.rerun()

                    # Inline edit form
                    if st.session_state.get("editing_concept") == c_id:
                        with st.form(f"cedit_form_{c_id}"):
                            e_label = st.text_input("Label:", value=c.get("label", ""))
                            e_domain = st.text_input("Domain:", value=c.get("domain", ""))
                            e_def = st.text_area("Definition:", value=c.get("definition", ""))
                            if st.form_submit_button("Save"):
                                kg.update_concept_metadata(c_id, label=e_label,
                                                           domain=e_domain, definition=e_def)
                                st.session_state["editing_concept"] = None
                                st.success("Updated.")
                                st.rerun()
                            if st.form_submit_button("Cancel"):
                                st.session_state["editing_concept"] = None
                                st.rerun()

            if len(concepts) > 50:
                st.caption(f"_Showing 50 of {len(concepts)} concepts_")

        # Rhizomatic linking
        if len(concepts) >= 2:
            st.markdown("---")
            st.markdown("**Connect Rhizomatic Concepts**")
            with st.form("rhizome_form"):
                con_a = st.selectbox("First Concept", options=concept_ids,
                                     format_func=lambda x: concept_options.get(x, x))
                rel = st.selectbox("Relationship",
                                   ["critiques", "extends", "redefines",
                                    "reappropriates", "related_to"])
                con_b = st.selectbox("Second Concept", options=concept_ids,
                                     format_func=lambda x: concept_options.get(x, x))
                if st.form_submit_button("Apply Connection", use_container_width=True):
                    if con_a != con_b:
                        kg.link_concepts_rhizomatic(con_a, con_b, rel)
                        st.success("Rhizome connection mapped.")
                        st.rerun()
                    else:
                        st.error("Cannot connect a concept to itself.")

        # New concept
        st.markdown("---")
        st.markdown("**New Concept:**")
        with st.form("new_concept"):
            nc_id = st.text_input("Identifier (e.g. cyborg_theory):").strip()
            nc_lbl = st.text_input("Display Name:").strip()
            nc_dom = st.text_input("Domain:").strip()
            nc_def = st.text_area("Definition:").strip()
            if st.form_submit_button("Register", use_container_width=True):
                if nc_id and nc_lbl:
                    kg.add_concept_node(f"concept:{nc_id.lower()}", label=nc_lbl,
                                        domain=nc_dom, definition=nc_def)
                    st.success("Concept created.")
                    st.rerun()

    # ------------------------------------------------------------------
    # 2. Agent Management
    # ------------------------------------------------------------------
    with st.expander("👤 Agents"):
        if agents:
            st.markdown("**Existing Agents:**")
            for a in agents:
                a_id = a["id"]
                with st.container():
                    st.markdown(f"- **{a.get('name', a_id)}** — Role: *{a.get('role', '—')}*")
                    col_a, col_b = st.columns([1, 1])
                    with col_a:
                        if st.button("✏️ Edit", key=f"aedit_{a_id}"):
                            st.session_state[f"editing_agent"] = a_id
                    with col_b:
                        if st.button("🗑️", key=f"adel_{a_id}"):
                            kg.remove_node(a_id)
                            st.success("Agent deleted.")
                            st.rerun()

                    if st.session_state.get("editing_agent") == a_id:
                        with st.form(f"aedit_form_{a_id}"):
                            ea_name = st.text_input("Name:", value=a.get("name", ""))
                            ea_role = st.selectbox("Role:", ["author", "translator"],
                                                   index=["author", "translator"].index(a.get("role", "author")))
                            if st.form_submit_button("Save"):
                                kg.update_agent_node(a_id, name=ea_name, role=ea_role)
                                st.session_state["editing_agent"] = None
                                st.success("Updated.")
                                st.rerun()
                            if st.form_submit_button("Cancel"):
                                st.session_state["editing_agent"] = None
                                st.rerun()

        st.markdown("---")
        st.markdown("**New Agent:**")
        with st.form("new_agent"):
            na_id = st.text_input("Short ID (e.g. haraway):").strip()
            na_name = st.text_input("Full Name:").strip()
            na_role = st.selectbox("Role:", ["author", "translator"])
            if st.form_submit_button("Register", use_container_width=True):
                if na_id and na_name:
                    kg.add_agent_node(na_id, na_name, role=na_role)
                    st.success("Agent registered.")
                    st.rerun()

    # ------------------------------------------------------------------
    # 3. Source Text Management
    # ------------------------------------------------------------------
    with st.expander("📖 Source Texts"):
        if sources:
            st.markdown("**Existing Sources:**")
            for s in sources:
                s_id = s["id"]
                with st.container():
                    st.markdown(f"- **{s.get('title', s_id)}** — Year: *{s.get('year', '—')}*")
                    col_a, col_b = st.columns([1, 1])
                    with col_a:
                        if st.button("✏️ Edit", key=f"sedit_{s_id}"):
                            st.session_state[f"editing_source"] = s_id
                    with col_b:
                        if st.button("🗑️", key=f"sdel_{s_id}"):
                            kg.remove_node(s_id)
                            st.success("Source deleted.")
                            st.rerun()

                    if st.session_state.get("editing_source") == s_id:
                        with st.form(f"sedit_form_{s_id}"):
                            es_title = st.text_input("Title:", value=s.get("title", ""))
                            es_year = st.number_input("Year:", min_value=1800, max_value=2030,
                                                      value=s.get("year") or 2000)
                            es_auth = st.selectbox("Author:", options=["_none"] + agent_ids,
                                                   format_func=lambda x: (
                                                       "None" if x == "_none" else agent_options.get(x, x)))
                            if st.form_submit_button("Save"):
                                auth_val = None if es_auth == "_none" else es_auth
                                kg.update_source_text_node(s_id, title=es_title,
                                                           year=es_year, author_id=auth_val)
                                st.session_state["editing_source"] = None
                                st.success("Updated.")
                                st.rerun()
                            if st.form_submit_button("Cancel"):
                                st.session_state["editing_source"] = None
                                st.rerun()

        st.markdown("---")
        st.markdown("**New Source Text:**")
        with st.form("new_source"):
            ns_id = st.text_input("Short ID (e.g. dialektika):").strip()
            ns_title = st.text_input("Title:").strip()
            ns_year = st.number_input("Year:", min_value=1800, max_value=2030, value=2000)
            ns_auth = st.selectbox("Author:", options=["_none"] + agent_ids,
                                   format_func=lambda x: (
                                       "None" if x == "_none" else agent_options.get(x, x)))
            if st.form_submit_button("Register", use_container_width=True):
                if ns_id and ns_title:
                    auth_val = None if ns_auth == "_none" else ns_auth
                    kg.add_source_text_node(ns_id, title=ns_title,
                                            author_id=auth_val, year=ns_year)
                    st.success("Source registered.")
                    st.rerun()

    # ------------------------------------------------------------------
    # 4. Lineage Cleanup
    # ------------------------------------------------------------------
    with st.expander("🧹 Lineage Cleanup"):
        st.write("Merge messy imported lineages into clean conceptual ones.")

        all_lineages = kg.get_all_lineages()
        if all_lineages:
            with st.form("lineage_cleanup_form"):
                messy_selections = st.multiselect(
                    "Select messy lineages to merge:",
                    options=all_lineages,
                    help="Select all raw lineages that belong to the same conceptual category",
                )
                clean_name = st.text_input(
                    "Merge into:",
                    placeholder="e.g. Lacanian Psychoanalysis",
                ).strip()

                if st.form_submit_button("Unify Lineages", use_container_width=True):
                    if messy_selections and clean_name:
                        changes = kg.merge_lineages(messy_selections, clean_name)
                        st.success(
                            f"Unified {changes} mappings from "
                            f"{len(messy_selections)} lineages into '{clean_name}'."
                        )
                        st.rerun()
                    else:
                        st.error("Select at least one lineage and provide a target name.")

            if glossary.entries:
                if st.button(
                    "🪄 Auto-Align to Glossary",
                    use_container_width=True,
                    help="Match chaotic lineages against your manual glossary terms",
                ):
                    aligned = kg.bulk_align_lineages_with_glossary(glossary.entries)
                    st.success(f"Auto-aligned {aligned} translations.")
                    st.rerun()
        else:
            st.info("No lineages registered yet.")