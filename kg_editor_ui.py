# kg_editor_ui.py
#
# High-Efficiency, Clean Workspace for Knowledge Graph Curation
#

import sys
from pathlib import Path

import streamlit as st

# Ensure backend imports are accessible
sys.path.append(str(Path(__file__).parent))

from translate_core.glossary import Glossary
from translate_core.knowledge_graph import KnowledgeGraph

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
agent_options = {a["id"]: a["name"] for a in agents}
agent_ids = list(agent_options.keys()) if agent_options else [None]
if not agent_options:
    agent_options = {None: "(No agents registered yet)"}

sources = kg.get_all_by_type("source_text")
source_options = {s["id"]: s["title"] for s in sources}
source_ids = list(source_options.keys()) if source_options else [None]
if not source_options:
    source_options = {None: "(No source texts registered yet)"}

concepts = kg.get_all_by_type("concept")
concept_options = {c["id"]: c["label"] for c in concepts}
concept_ids = list(concept_options.keys()) if concept_options else [None]
if not concept_options:
    concept_options = {None: "(No concepts registered yet)"}

# ---------------------------------------------------------------------------
# Main Layout: Two Columns (Equal Spacing, No Squeezed Sub-Columns)
# ---------------------------------------------------------------------------
workspace_col, utility_col = st.columns([1.0, 1.0], gap="large")

# ===========================================================================
# WORKSPACE COLUMN (Left: Search, Inline Edits)
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
                    f"### Term: `{match.get('term')}` (`{match.get('lang').upper()}`)"
                )
                st.caption(
                    f"Node ID: {term_id} | Animate: {match.get('is_animate', False)}"
                )

                # Inline delete term button
                if st.button("🗑️ Delete Term", key=f"del_{term_id}"):
                    kg.remove_node(term_id)
                    st.success("Term deleted.")
                    st.rerun()

                translations = match.get("translations", [])
                st.markdown("**Translations & Inline Lineage Editing:**")
                if not translations:
                    st.info("No contextual translations mapped to this term.")
                else:
                    for idx, t in enumerate(translations):
                        tgt_lemma = t.get("lemma", "")
                        lineage = t.get("lineage", "general")
                        mapping_key = f"map:term:en:{match.get('term').lower()}>>term:sl:{tgt_lemma.lower()}:{lineage.lower().replace(' ', '_')}"

                        st.markdown(
                            f"👉 **`{t.get('term')}`** (Lineage: *{lineage}* | Confidence: `{t.get('confidence'):.2f}`)"
                        )
                        if t.get("sources") or t.get("agents"):
                            st.caption(
                                f"└ *Context:* {', '.join(t.get('sources') + t.get('agents'))}"
                            )

                        # Inline translation editor (Clean vertical stack)
                        with st.form(f"inline_edit_{mapping_key}_{idx}"):
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
                                    mapping_key,
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
                                kg.remove_node(mapping_key)
                                st.success("Mapping deleted.")
                                st.rerun()
                        st.markdown("---")
    else:
        # Default view (no query entered): Display clean vertical Quick Link Form
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
                options=[None] + source_ids,
                format_func=lambda x: (
                    "No source reference" if x is None else source_options[x]
                ),
            )
            new_agent = st.selectbox(
                "Attribute to Translator/Author:",
                options=[None] + agent_ids,
                format_func=lambda x: (
                    "No attribution" if x is None else agent_options[x]
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
                        source_text_id=new_source,
                        agent_id=new_agent,
                        verified=True,
                    )
                    st.success(
                        f"Contextual mapping created: '{new_src}' ➔ '{new_tgt}'."
                    )
                    st.rerun()
                else:
                    st.error("Both English and Slovenian terms are required.")

# ===========================================================================
# UTILITY COLUMN (Right: Concept Links & Metadata Management)
# ===========================================================================
with utility_col:
    st.subheader("🌿 Concept Rhizomes & Entities")

    # 1. Concept Rhizomatic Linking
    st.markdown("#### Connect Rhizomatic Concepts")
    if len(concepts) >= 2:
        with st.form("rhizome_form"):
            con_a = st.selectbox(
                "First Concept",
                options=concept_ids,
                format_func=lambda x: concept_options[x],
            )
            rel = st.selectbox(
                "Relationship Type",
                ["critiques", "extends", "redefines", "reappropriates", "related_to"],
            )
            con_b = st.selectbox(
                "Second Concept",
                options=concept_ids,
                format_func=lambda x: concept_options[x],
            )

            if st.form_submit_button(
                "Apply Rhizomatic Connection", use_container_width=True
            ):
                if con_a != con_b:
                    kg.link_concepts_rhizomatic(con_a, con_b, rel)
                    st.success("Rhizome connection mapped successfully.")
                    st.rerun()
                else:
                    st.error("Cannot connect a concept to itself.")
    else:
        st.info("Register some concept nodes via manual mapping to form relationships.")

    # 2. Add New Concept Definition Box
    st.markdown("#### Define Standalone Concept Container")
    with st.form("standalone_concept"):
        new_c_id = st.text_input("Concept Identifier (ID, e.g. cyborg_theory):").strip()
        new_c_lbl = st.text_input("Concept Display Name (e.g. Cyborg Theory):").strip()
        new_c_dom = st.text_input("Domain Category (e.g. Feminist Philosophy):").strip()
        new_c_def = st.text_area("Scope Definition:").strip()

        if st.form_submit_button("Register Concept", use_container_width=True):
            if new_c_id and new_c_lbl:
                kg.add_concept_node(
                    f"concept:{new_c_id.lower()}",
                    label=new_c_lbl,
                    domain=new_c_dom,
                    definition=new_c_def,
                )
                st.success("Concept defined.")
                st.rerun()

    st.markdown("---")

    # 3. Quick Metadata Register (Authors, Books)
    st.markdown("#### Meta Registers")

    with st.expander("👤 Register Author / Translator"):
        with st.form("add_agent"):
            tag_id = st.text_input("Short ID (e.g. haraway):").strip()
            tag_name = st.text_input("Full Name (e.g. Donna Haraway):").strip()
            tag_role = st.selectbox("Default Role:", ["author", "translator"])
            if st.form_submit_button("Save Agent", use_container_width=True):
                if tag_id and tag_name:
                    kg.add_agent_node(tag_id, tag_name, role=tag_role)
                    st.success("Agent registered.")
                    st.rerun()

    with st.expander("📖 Register Source Text / Book"):
        with st.form("add_source"):
            src_id = st.text_input("Short ID (e.g. dialektika):").strip()
            src_title = st.text_input("Book / Essay Title:").strip()
            src_year = st.number_input(
                "Year of Publication:", min_value=1800, max_value=2030, value=2000
            )
            src_auth = st.selectbox(
                "Select Author Reference:",
                options=agent_ids,
                format_func=lambda x: agent_options[x],
            )
            if st.form_submit_button("Save Text", use_container_width=True):
                if src_id and src_title:
                    kg.add_source_text_node(
                        src_id, title=src_title, author_id=src_auth, year=src_year
                    )
                    st.success("Source Text registered.")
                    st.rerun()

    st.markdown("---")

    # 4. Chaotic Lineage Cleanup Station
    st.markdown("#### 🧹 Lineage Cleanup Station")
    st.write(
        "Merge messy, raw imported client filenames into clean conceptual lineages."
    )

    all_lineages = kg.get_all_lineages()
    if all_lineages:
        with st.form("lineage_cleanup_form"):
            messy_selections = st.multiselect(
                "Select messy filenames / lineages to merge:",
                options=all_lineages,
                help="Select all project codes or raw filenames that belong to the same lineage",
            )
            clean_name = st.text_input(
                "Merge selected into a single clean lineage name:",
                placeholder="e.g. Lacanian Psychoanalysis, Art History, Marxist Theory",
            ).strip()

            if st.form_submit_button("Unified Lineages", use_container_width=True):
                if messy_selections and clean_name:
                    changes = kg.merge_lineages(messy_selections, clean_name)
                    st.success(
                        f"Unified {changes} translation mappings from {len(messy_selections)} raw lineages into '{clean_name}'."
                    )
                    st.rerun()
                else:
                    st.error(
                        "Please select at least one messy lineage and provide a clean target name."
                    )

        # Bulk Align button
        if glossary.entries:
            if st.button(
                "🪄 Auto-Align Messy Mappings to Manual Glossary",
                use_container_width=True,
                help="Scans all chaotic files, matches them against your manual glossary terms, and auto-tags their lineages",
            ):
                aligned = kg.bulk_align_lineages_with_glossary(glossary.entries)
                st.success(
                    f"Successfully auto-aligned {aligned} translations to your manual glossary classifications."
                )
                st.rerun()
