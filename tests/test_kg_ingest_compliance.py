"""Compliance tests for translate_core/kg_ingest_entities.write_to_kg.

Exercises each ontology repair listed in data/ontology_audit/kg-sink.md:
- §1 factory-only writes (no direct kg.G.add_edge from this module)
- §2.4.1 container / cited project_type allowlists
- §2.5 agent role allowlist + O-12 quartet on every agent write
- §2.6 institution kind allowlist
- §3.2 translation_published_by edge wired via factory
- §4 invariant 1 slugify discipline on every source_text id
- §4 invariant 6 citation_style allowlist
- O-20 — container source_text never written without a translator
- dead-code removal of wire_doc_pair_bridge_edges

Uses an in-memory KnowledgeGraph backed by a tmp_path file; never touches
data/knowledge.db.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import pytest

from translate_core import kg_ingest_entities as ingest
from translate_core.knowledge_graph import KnowledgeGraph

INGEST_FILE = str(Path(ingest.__file__).resolve())


def _rec(kind: str, payload: dict, *, provenance: str | None = None) -> dict:
    source: dict = {"origin": "tmx-test", "segment_idx": 0}
    if provenance is not None:
        source["provenance"] = provenance
    return {
        "kind": kind,
        "tier": "direct_write",
        "payload": payload,
        "source": source,
        "signals": {},
        "confidence": 0.95,
        "reason_codes": [],
    }


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(db_path=tmp_path / "kg.json")
    # keep test purely in-memory — never persist
    g.save = lambda: None  # type: ignore[method-assign]
    return g


@pytest.fixture
def add_edge_callers(kg):
    """Wrap kg.G.add_edge to record the immediate caller file of every call.
    Lets us assert that no direct call originates from kg_ingest_entities.
    """
    real_add_edge = kg.G.add_edge
    callers: List[str] = []

    def tracking_add_edge(*args, **kwargs):
        frame = sys._getframe(1)
        callers.append(frame.f_code.co_filename)
        return real_add_edge(*args, **kwargs)

    kg.G.add_edge = tracking_add_edge  # type: ignore[method-assign]
    return callers


@pytest.fixture
def records():
    """One record per branch; exercises every repaired site."""
    return [
        # translated_work — diacritic work_id, off-allowlist project_type,
        # full author/translator/publisher payload.
        _rec("translated_work", {
            "work_id": "Kunst življenja umetnosti",
            "title_orig": "Kunst življenja umetnosti",
            "title_translation": "Art of the Life of Art",
            "orig_lang": "sl",
            "translation_lang": "en",
            "project_type": "memoir",  # off-allowlist → book_translation
            "author": "Boris Groys",
            "translator": "Jelka Bajt",
            "publisher": "Maska",
        }, provenance="cobiss_personal"),
        # translated_work — no translator → must NOT be written; sent to review.
        _rec("translated_work", {
            "work_id": "no-translator-book",
            "title_orig": "Untranslated",
            "author": "Anon",
        }, provenance="cobiss_personal"),
        # agent_person — off-allowlist role → coerced to "agent".
        _rec("agent_person", {
            "name": "Žan Doe",
            "role": "choreographer-or-something",  # off-allowlist
            "dedup_group": "z.doe",
            "alt_spellings": ["Žan Doe"],
            "all_roles": ["choreographer-or-something"],
            "mention_count": 2,
        }, provenance="tm_smol"),
        # institution — off-allowlist kind → coerced to "other".
        _rec("institution", {
            "name": "Acme Press",
            "kind": "press",
        }, provenance="tm_smol"),
        # cited_work — diacritic cited_id, off-allowlist project_type,
        # off-allowlist citation_style, translation edition (exercises
        # link_translation_published_by, post-Phase-4 rename).
        _rec("cited_work", {
            "cited_id": "Foucault Discipliné",
            "title_orig": "Discipline and Punish",
            "title_translation": "Nadzor in kazen",
            "orig_lang": "en",
            "translation_lang": "sl",
            "project_type": "memoir",  # off-allowlist → cited_work
            "citation_style": "harvard",  # off-allowlist → dropped
            "author": "Michel Foucault",
            "original_pub": {"publisher": "Vintage", "city": "New York"},
            "translation_edition": {"publisher": "Krtina", "city": "Ljubljana"},
        }, provenance="tm_smol"),
        # artwork — diacritic work_id, artist with diacritics.
        _rec("artwork", {
            "work_id": "Hommage à Marko",
            "title_en": "Homage to Marko",
            "artist": "Žiga Artist",
            "medium": "oil on canvas",
        }, provenance="tm_smol"),
    ]


@pytest.fixture
def ran(kg, records, add_edge_callers, tmp_path):
    review_path = tmp_path / "review.json"
    dropped_path = tmp_path / "dropped.jsonl"
    stats = ingest.write_to_kg(
        kg,
        records,
        review_path=review_path,
        dropped_path=dropped_path,
        dry_run=False,
    )
    return {
        "kg": kg,
        "stats": stats,
        "callers": add_edge_callers,
        "review_path": review_path,
        "dropped_path": dropped_path,
    }


# ----- O-12 quartet on every agent (§2.5) -----------------------------------

def test_every_agent_has_o12_quartet(ran):
    """All 4 O-12 fields are present and non-empty on every agent node.

    Covers: Pass 2 author bare-agent, Pass 2 translator bare-agent,
    Pass 3 cited_work author bare-agent, Pass 4 artwork artist bare-agent.
    """
    agents = [
        (nid, nd) for nid, nd in ran["kg"].G.nodes(data=True)
        if nd.get("type") == "agent"
    ]
    assert agents, "expected at least one agent node"
    for nid, nd in agents:
        assert nd.get("dedup_group"), f"{nid} missing dedup_group"
        assert nd.get("alt_spellings"), f"{nid} missing alt_spellings"
        assert nd.get("all_roles"), f"{nid} missing all_roles"
        assert nd.get("mention_count"), f"{nid} missing mention_count"


# ----- No direct kg.G.add_edge from kg_ingest_entities (§1) -----------------

def test_no_direct_add_edge_from_ingest_module(ran):
    """Every edge in the KG was written through a KnowledgeGraph factory.


    Covers: Pass 2 author edge, Pass 3 cited_work author edge,
    Pass 3 translation_published_by edge, Pass 4 artwork artist edge.
    """
    direct = [f for f in ran["callers"] if f == INGEST_FILE]
    assert direct == [], (
        f"direct kg.G.add_edge calls from kg_ingest_entities: {direct}"
    )


# ----- O-20: containers carry translated_by OR went to review --------------

def test_container_requires_translated_by_edge(ran):
    """Container source_texts have ≥1 translated_by edge; otherwise the
    record must have been routed to the review queue, not the KG."""
    kg = ran["kg"]
    container_types = ingest.CONTAINER_TYPES
    for nid, nd in kg.G.nodes(data=True):
        if nd.get("type") != "source_text":
            continue
        if nd.get("project_type") not in container_types:
            continue
        out_relations = {
            ed.get("relation") for _u, _v, ed in kg.G.out_edges(nid, data=True)
        }
        assert "translated_by" in out_relations, (
            f"container {nid} missing translated_by edge"
        )
    # And the no-translator record must NOT have produced a node.
    assert not kg.G.has_node("source:no-translator-book"), (
        "translated_work without translator must be rejected, not written"
    )
    # …but it should be in the review queue.
    import json
    review = json.loads(ran["review_path"].read_text(encoding="utf-8"))
    review_ids = [r["payload"]["work_id"] for r in review if r["kind"] == "translated_work"]
    assert "no-translator-book" in review_ids


# ----- Allowlist coercions --------------------------------------------------

def test_off_allowlist_role_coerced_to_agent(ran):
    """agent_person with an off-allowlist role is written as role='agent'."""
    node = ran["kg"].G.nodes.get("agent:zan-doe")
    assert node is not None, "expected slugified agent node"
    assert node["role"] == "agent"


def test_off_allowlist_institution_kind_coerced_to_other(ran):
    """institution with kind='press' (not in §2.6 set) becomes kind='other'."""
    node = ran["kg"].G.nodes.get("institution:acme-press")
    assert node is not None
    assert node["kind"] == "other"


def test_off_allowlist_container_project_type_coerced(ran):
    """translated_work payload with project_type='memoir' (off-allowlist)
    is written as project_type='book_translation'."""
    nid = f"source:{ingest._slugify('Kunst življenja umetnosti')}"
    node = ran["kg"].G.nodes.get(nid)
    assert node is not None, f"expected container node {nid}"
    assert node["project_type"] == "book_translation"


def test_off_allowlist_cited_project_type_coerced(ran):
    """cited_work payload with project_type='memoir' is coerced to 'cited_work'."""
    nid = f"source:{ingest._slugify('Foucault Discipliné')}"
    node = ran["kg"].G.nodes.get(nid)
    assert node is not None, f"expected cited node {nid}"
    assert node["project_type"] == "cited_work"


def test_off_allowlist_citation_style_dropped(ran):
    """citation_style='harvard' (not in §4 invariant 6 set) is NOT stored."""
    nid = f"source:{ingest._slugify('Foucault Discipliné')}"
    node = ran["kg"].G.nodes.get(nid)
    assert node is not None
    assert "citation_style" not in node, (
        f"citation_style must be dropped when off-allowlist; got {node.get('citation_style')!r}"
    )


# ----- Slugify discipline on every source_text id (§4 invariant 1) ---------

def test_source_text_ids_are_slugified(ran):
    """Raw mixed-case / diacritic ids never survive into source_text node ids.

    Covers: translated_work work_id slugify, cited_work cited_id slugify,
    artwork work_id slugify.
    """
    expected = {
        "source:" + ingest._slugify("Kunst življenja umetnosti"),
        "source:" + ingest._slugify("Foucault Discipliné"),
        "source:" + ingest._slugify("Hommage à Marko"),
    }
    for sid in expected:
        assert ran["kg"].G.has_node(sid), f"missing slugified node {sid}"
        # slug is lowercase ASCII / digits / hyphens only
        local = sid.split(":", 1)[1]
        assert all(c.isascii() and (c.isalnum() or c == "-") for c in local), (
            f"node id {sid} retains non-ASCII / non-slug characters"
        )
    # None of the diacritic raw forms survive as node ids.
    for raw in (
        "source:Kunst življenja umetnosti",
        "source:kunst življenja umetnosti",
        "source:Foucault Discipliné",
        "source:Hommage à Marko",
    ):
        assert not ran["kg"].G.has_node(raw)


# ----- translation_published_by edge wired via factory --------------------

def test_translation_published_by_edge_present(ran):
    """The cited_work with a translation_edition publisher gets a
    translation_published_by edge — proves link_translation_published_by was
    used, not a direct add_edge."""
    kg = ran["kg"]
    src = "source:" + ingest._slugify("Foucault Discipliné")
    inst = "institution:" + ingest._slugify("Krtina")
    assert kg.G.has_edge(src, inst)
    assert kg.G[src][inst].get("relation") == "translation_published_by"


# ----- Dead code removed ----------------------------------------------------

def test_wire_doc_pair_bridge_edges_removed():
    """wire_doc_pair_bridge_edges had no callers in the repo; it is
    removed per the audit's Dead code section."""
    assert not hasattr(ingest, "wire_doc_pair_bridge_edges")


# ----- Factory-level O-12 enforcement (Phase 1) --------------------------

def test_add_agent_node_fills_o12_quartet_when_missing(kg):
    """The factory defaults dedup_group, alt_spellings, all_roles, mention_count."""
    kg.add_agent_node("foo-bar", name="Foo Bar", role="author")
    node = kg.G.nodes["agent:foo-bar"]
    assert node["dedup_group"]
    assert node["alt_spellings"] == ["Foo Bar"]
    assert node["all_roles"] == ["author"]
    assert node["mention_count"] == 1


def test_update_agent_node_maintains_o12_quartet(kg):
    """Renaming an agent recomputes dedup_group and unions into alt_spellings."""
    kg.add_agent_node("foo-bar", name="Foo Bar", role="author")
    kg.update_agent_node("agent:foo-bar", name="Foo B. Bar", role="editor")
    node = kg.G.nodes["agent:foo-bar"]
    assert "Foo B. Bar" in node["alt_spellings"]
    assert "editor" in node["all_roles"]
    assert node["dedup_group"]


def test_ensure_agent_produces_o12_compliant_node(kg):
    """The shared helper now emits a fully O-12-compliant agent node."""
    from translate_core.entity_extraction.ingest_helpers import ensure_agent

    ensure_agent(kg, "Jane Q. Public")
    node = kg.G.nodes["agent:jane-q-public"]
    assert node["dedup_group"]
    assert node["alt_spellings"]
    assert node["all_roles"]
    assert node["mention_count"] == 1


# ----- Legacy bilingual params rejected (Phase 3) -------------------------

def test_update_source_text_node_rejects_legacy_bilingual_params(kg):
    """title_en/title_sl/slovenian_edition are no longer accepted."""
    kg.add_source_text_node("x", title="X", project_type="book")
    with pytest.raises(TypeError):
        kg.update_source_text_node("source:x", title_en="Y")


# ----- link_appears_in factory (Phase 2) ---------------------------------

def test_link_appears_in_wires_chapter_to_book(kg):
    """The new factory creates an appears_in edge from chapter to container."""
    kg.add_source_text_node("book", title="Book", project_type="book")
    kg.add_source_text_node("chapter", title="Chapter", project_type="book_chapter")
    assert kg.link_appears_in("chapter", "book") is True
    assert kg.G.has_edge("source:chapter", "source:book")
    assert kg.G["source:chapter"]["source:book"].get("relation") == "appears_in"


def test_link_appears_in_self_loop_returns_false(kg):
    """O-17: chapter and book must be distinct."""
    kg.add_source_text_node("book", title="Book", project_type="book")
    assert kg.link_appears_in("book", "book") is False


# ----- O-3: update_translation_mapping verified is monotonic ----------------

def test_update_translation_mapping_verified_monotonic(kg):
    """O-3: update_translation_mapping must never downgrade verified True→False."""
    kg.add_term_node("test", "en")
    kg.add_term_node("test", "sl")
    mapping_id = kg.link_translations_with_context(
        "term:en:test", "term:sl:test", confidence=1.0, verified=True
    )
    assert kg.G.nodes[mapping_id]["verified"] is True
    # Attempt to downgrade to False — must NOT succeed
    kg.update_translation_mapping(mapping_id, verified=False)
    assert kg.G.nodes[mapping_id]["verified"] is True, \
        "O-3 violation: verified was downgraded True→False"
