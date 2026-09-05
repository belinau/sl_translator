"""End-to-end smoke tests for concept attribution through extract_and_ingest.

Verifies the full flow: fake extractor returns a concept entity with an
evidence_span → build_record → attribution_verifier → score_all →
dedup_records → write_to_kg. Asserts that:

* a verified (roster-confirmed) attribution writes the concept node AND
  the attributed_to edge, with no review entry;
* an ungrounded/fabricated attribution writes the concept node but
  WITHHOLDS the attributed_to edge and queues an attribution_pending
  review entry;
* an un-rostered concept confirmed by the second model pass writes the
  edge;
* the commit_record concept branch (curator confirm of a queued
  attribution) creates the attributed_to edge.
"""
from __future__ import annotations

import json


import pytest

from translate_core.citation_collector import CitationSnippet, extract_and_ingest
from translate_core.kg_review_ops import commit_record


@pytest.fixture
def kg(tmp_path):
    from translate_core.knowledge_graph import KnowledgeGraph

    g = KnowledgeGraph(db_path=tmp_path / "kg.json")
    g.save = lambda: None  # type: ignore[method-assign]
    return g


def _snippet(src: str, tgt: str, **kw) -> CitationSnippet:
    defaults = dict(
        text=src,
        origin="editor_en-sl_test_project",
        segment_idx=1,
        format="body",
        container_work_id="test-container",
        target_text=tgt,
    )
    defaults.update(kw)
    return CitationSnippet(**defaults)


def _concept_entity(*, label, author, evidence_span, domain="feminism"):
    return [{
        "kind": "concept",
        "label_orig": label,
        "label_translation": label,  # bilingual → direct-write the node
        "orig_lang": "en",
        "translation_lang": "sl",
        "domain": domain,
        "originating_author": author,
        "evidence_span": evidence_span,
    }]


# ── verified (roster-confirmed) → node + edge, no review ─────────────────────

def test_verified_roster_attribution_writes_edge(kg, tmp_path):
    # concept:empowerment → "bell hooks" in data/concept_theorists.json
    src = "As bell hooks shows, empowerment is a practice of freedom."
    tgt = "Kot kaže bell hooks, je opolnomočenje praksa svobode."
    report = extract_and_ingest(
        [_snippet(src, tgt)],
        kg,
        review_path=str(tmp_path / "review.json"),
        dropped_path=str(tmp_path / "dropped.jsonl"),
        extractor=lambda **kw: _concept_entity(
            label="empowerment", author="bell hooks",
            evidence_span="bell hooks shows, empowerment",
        ),
        model_verify=lambda *a: True,  # should NOT be called (roster hit)
    )
    assert report.written >= 1
    assert report.queued == 0
    assert kg.G.has_node("concept:empowerment")
    # The attributed_to edge concept → agent must exist.
    ag = "agent:bell-hooks"
    assert kg.G.has_node(ag)
    assert kg.G.has_edge("concept:empowerment", ag)
    assert kg.G.edges["concept:empowerment", ag]["relation"] == "attributed_to"


# ── fabricated span → node written, edge withheld, review queued ─────────────

def test_fabricated_span_withholds_edge_and_queues_review(kg, tmp_path):
    src = "An unrelated segment about dance and choreography."
    tgt = "Nesorodni stavek o plesu in koreografiji."
    rev = tmp_path / "review.json"
    report = extract_and_ingest(
        [_snippet(src, tgt)],
        kg,
        review_path=str(rev),
        dropped_path=str(tmp_path / "dropped.jsonl"),
        extractor=lambda **kw: _concept_entity(
            label="empowerment", author="bell hooks",
            evidence_span="bell hooks's empowerment framework",  # not in text
        ),
        model_verify=lambda *a: True,
    )
    # Concept node direct-writes (bilingual), but the edge is withheld.
    assert kg.G.has_node("concept:empowerment")
    assert not kg.G.has_node("agent:bell-hooks")
    assert not kg.G.has_edge("concept:empowerment", "agent:bell-hooks")
    # An attribution_pending review entry was queued.
    assert report.queued == 1
    queue = json.loads(rev.read_text(encoding="utf-8"))
    assert any(r.get("kind") == "concept"
               and r.get("payload", {}).get("attribution_pending")
               for r in queue)


# ── un-rostered + model yes → edge written ───────────────────────────────────

def test_unrostered_model_confirmed_writes_edge(kg, tmp_path):
    src = "Jane Roe's framework of pluriversal design rethinks worlds."
    tgt = "Okvir Jane Roe o pluriverzalnem oblikovanju."
    report = extract_and_ingest(
        [_snippet(src, tgt)],
        kg,
        review_path=str(tmp_path / "review.json"),
        dropped_path=str(tmp_path / "dropped.jsonl"),
        extractor=lambda **kw: _concept_entity(
            label="pluriversal design", author="Jane Roe",
            evidence_span="Jane Roe's framework of pluriversal design",
            domain="philosophy",
        ),
        model_verify=lambda *a: True,
    )
    assert report.written >= 1
    assert kg.G.has_node("concept:pluriversal-design")
    assert kg.G.has_edge("concept:pluriversal-design", "agent:jane-roe")


def test_unrostered_model_rejected_withholds_edge(kg, tmp_path):
    src = "Jane Roe's framework of pluriversal design rethinks worlds."
    tgt = "Okvir Jane Roe o pluriverzalnem oblikovanju."
    rev = tmp_path / "review.json"
    report = extract_and_ingest(
        [_snippet(src, tgt)],
        kg,
        review_path=str(rev),
        dropped_path=str(tmp_path / "dropped.jsonl"),
        extractor=lambda **kw: _concept_entity(
            label="pluriversal design", author="Jane Roe",
            evidence_span="Jane Roe's framework of pluriversal design",
            domain="philosophy",
        ),
        model_verify=lambda *a: False,
    )
    assert kg.G.has_node("concept:pluriversal-design")
    assert not kg.G.has_edge("concept:pluriversal-design", "agent:jane-roe")
    assert report.queued == 1


# ── curator confirm of a queued attribution creates the edge ─────────────────

def test_commit_record_concept_creates_attributed_to_edge(kg, tmp_path):
    src = "An unrelated segment about dance and choreography."
    tgt = "Nesorodni stavek o plesu."
    rev = tmp_path / "review.json"
    extract_and_ingest(
        [_snippet(src, tgt)],
        kg,
        review_path=str(rev),
        dropped_path=str(tmp_path / "dropped.jsonl"),
        extractor=lambda **kw: _concept_entity(
            label="empowerment", author="bell hooks",
            evidence_span="bell hooks's empowerment framework",
        ),
        model_verify=lambda *a: True,
    )
    queue = json.loads(rev.read_text(encoding="utf-8"))
    pending = next(r for r in queue
                   if r.get("kind") == "concept"
                   and r.get("payload", {}).get("attribution_pending"))
    # Curator confirms → the edge is created.
    err = commit_record(kg, pending)
    assert err is None
    assert kg.G.has_node("agent:bell-hooks")
    assert kg.G.has_edge("concept:empowerment", "agent:bell-hooks")
    assert kg.G.edges["concept:empowerment", "agent:bell-hooks"]["relation"] == "attributed_to"