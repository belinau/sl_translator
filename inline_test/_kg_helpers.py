"""KG seeding helpers for inline tests.

The conftest monkeypatch reduces `KnowledgeGraph.__init__` to an empty
`networkx.DiGraph` plus two empty `flashtext.KeywordProcessor` instances.
These helpers populate both halves consistently so any test that exercises
`extract_entities` (the new `_kg_query` path) and any test that walks the
graph directly (legacy assertions) see the same data.

The shape mirrors the production seeders in `translate_core/knowledge_graph.py`:
- term nodes carry `term`, `lang`, optional `display_form`, `variants`,
  `frequency`, `is_phrase`
- translation lives on edges: `src --has_mapping--> map --maps_to--> tgt`
- concept membership: `term --instantiates_concept--> concept`
"""
from __future__ import annotations


def make_kg():
    """Empty KG instance (the conftest fast-init wires up G + flashtext indices)."""
    from translate_core.knowledge_graph import KnowledgeGraph

    return KnowledgeGraph()


def seed_term(
    kg,
    lang: str,
    lemma: str,
    *,
    display_form: str | None = None,
    variants: tuple[str, ...] = (),
    frequency: int = 1,
    is_phrase: bool | None = None,
) -> str:
    """Add a term node and register all surface forms in the flashtext index.

    Returns the node id so callers can wire mappings or concepts to it.
    """
    if is_phrase is None:
        is_phrase = " " in lemma
    node_id = f"term:{lang}:{lemma}"
    kg.G.add_node(
        node_id,
        type="term",
        term=lemma,
        lang=lang,
        display_form=display_form,
        variants=list(variants),
        frequency=frequency,
        is_phrase=is_phrase,
    )
    # Index every surface form the user might type in source text.
    kg._exact_kp.add_keyword(lemma, node_id)
    if display_form and display_form != lemma:
        kg._exact_kp.add_keyword(display_form, node_id)
    for v in variants:
        if v and v != lemma:
            kg._exact_kp.add_keyword(v, node_id)
    return node_id


def seed_mapping(
    kg,
    src_id: str,
    tgt_id: str,
    *,
    confidence: float = 0.5,
    verified: bool = False,
    lineage: str = "general",
    register: str = "academic",
    gloss: str = "",
) -> str:
    """Wire src --has_mapping--> map --maps_to--> tgt.

    The mapping node carries the curator-authored translation metadata. This
    matches `KnowledgeGraph._get_translations` which is the traversal both
    the new `_kg_query` and the production code rely on.
    """
    map_id = f"map:{src_id}>>{tgt_id}:{lineage}"
    kg.G.add_node(
        map_id,
        type="translation_mapping",
        confidence=confidence,
        verified=verified,
        lineage=lineage,
        register=register,
        gloss=gloss,
    )
    kg.G.add_edge(src_id, map_id, relation="has_mapping")
    kg.G.add_edge(map_id, tgt_id, relation="maps_to")
    return map_id


def seed_concept(
    kg,
    slug: str,
    label: str,
    *,
    domain: str = "",
    terms: tuple[str, ...] = (),
) -> str:
    """Add a concept node and link the given term ids to it."""
    cid = f"concept:{slug}"
    kg.G.add_node(cid, type="concept", id=cid, label=label, domain=domain)
    for tid in terms:
        kg.G.add_edge(tid, cid, relation="instantiates_concept")
    return cid


def seed_pair(
    kg,
    src_lang: str,
    src_lemma: str,
    tgt_lang: str,
    tgt_lemma: str,
    *,
    src_display: str | None = None,
    src_variants: tuple[str, ...] = (),
    tgt_display: str | None = None,
    confidence: float = 0.5,
    verified: bool = False,
    lineage: str = "general",
    src_frequency: int = 1,
    tgt_frequency: int = 1,
) -> tuple[str, str]:
    """Shorthand: seed both terms and a mapping between them."""
    src_id = seed_term(
        kg, src_lang, src_lemma,
        display_form=src_display, variants=src_variants, frequency=src_frequency,
    )
    tgt_id = seed_term(
        kg, tgt_lang, tgt_lemma,
        display_form=tgt_display, frequency=tgt_frequency,
    )
    seed_mapping(
        kg, src_id, tgt_id,
        confidence=confidence, verified=verified, lineage=lineage,
    )
    return src_id, tgt_id
