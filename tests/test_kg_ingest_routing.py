"""Phase 5 routing-dispatcher tests for translate_core/kg_ingest_entities.

Exercises the routing rules in phase5_blueprint.md §1:
- translated_work containers accept only `cobiss_personal` / `curator_extra`
- cited_work accepts any VALID_PROVENANCE but requires container resolution
  and a determinable language pair
- agent_person / institution / concept / artwork / performance accept any
  VALID_PROVENANCE; unknown provenance → review
- routed-to-review records appear in the review queue with a `_route_reason`
  matching the blueprint codes

All tests are designed to fail against current code (TDD red): the router
`_route_record` is not yet in place, so records that should route to review
are written directly today.

Test fixtures use `tmp_path`-scoped KnowledgeGraph; `kg.save` is monkey-
patched to a no-op. Non-SL/EN language pairs (de/fr, hr/sl, etc.) are used
where direction is not the discriminator.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from translate_core import kg_ingest_entities as ingest
from translate_core.knowledge_graph import KnowledgeGraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kg(tmp_path: Path) -> KnowledgeGraph:
    g = KnowledgeGraph(db_path=tmp_path / "kg.json")
    # Never touch data/knowledge.db.
    g.save = lambda: None  # type: ignore[method-assign]
    return g


@pytest.fixture
def review_path(tmp_path: Path) -> Path:
    return tmp_path / "extraction_review.json"


@pytest.fixture
def dropped_path(tmp_path: Path) -> Path:
    return tmp_path / "extraction_dropped.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(
    kind: str,
    payload: dict,
    *,
    provenance: Any = "__unset__",
    origin: str = "tmx-test",
    segment_idx: int = 0,
) -> dict:
    """Build a record with optional source.provenance.

    `provenance="__unset__"` (the default) means the key is omitted from
    `source` entirely (covers the "absent" branch). `provenance=None`
    sets it explicitly to None.
    """
    source: dict = {"origin": origin, "segment_idx": segment_idx}
    if provenance != "__unset__":
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


def _container_payload(
    *,
    work_id: str = "container-de-fr",
    orig_lang: str = "de",
    translation_lang: str = "fr",
    author: str = "Heinrich Mann",
    translator: str = "Aurore Dupont",
    publisher: str = "Editions Alpha",
) -> dict:
    return {
        "work_id": work_id,
        "title_orig": "Untertan",
        "title_translation": "Le Sujet",
        "orig_lang": orig_lang,
        "translation_lang": translation_lang,
        "project_type": "book_translation",
        "author": author,
        "translator": translator,
        "publisher": publisher,
    }


def _cited_payload(
    *,
    cited_id: str = "cited-hr-sl",
    container_work_id: str | None = None,
    orig_lang: str | None = "hr",
    translation_lang: str | None = "sl",
    author: str = "Krleža",
    title_orig: str = "Povratak Filipa Latinovicza",
    title_translation: str = "Vrnitev Filipa Latinovicza",
) -> dict:
    p: dict = {
        "cited_id": cited_id,
        "title_orig": title_orig,
        "title_translation": title_translation,
        "orig_lang": orig_lang,
        "translation_lang": translation_lang,
        "project_type": "book",
        "author": author,
    }
    if container_work_id is not None:
        p["container_work_id"] = container_work_id
    return p


def _agent_payload(
    *,
    name: str = "Anton Novak",
    role: str = "author",
    dedup_group: str = "a.novak",
) -> dict:
    return {
        "name": name,
        "role": role,
        "dedup_group": dedup_group,
        "alt_spellings": [name],
        "all_roles": [role],
        "mention_count": 1,
    }


def _run(
    kg: KnowledgeGraph,
    records: list[dict],
    review_path: Path,
    dropped_path: Path,
) -> ingest.IngestStats:
    return ingest.write_to_kg(
        kg,
        records,
        review_path=review_path,
        dropped_path=dropped_path,
        dry_run=False,
    )


def _load_review(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _route_reasons_for(review: list[dict], kind: str) -> list[str]:
    return [r.get("_route_reason") for r in review if r.get("kind") == kind]


def _assert_route(
    record: dict,
    expected_decision: str,
    *,
    container_index: set[str] | None = None,
    expected_reason: str | None = None,
) -> None:
    """Unit-level assertion against `ingest._route_record`.

    Forces every test to fail when `_route_record` is not yet implemented
    (TDD red), independent of whether the integration path through
    `write_to_kg` happens to produce the right side-effect on current code.
    """
    decision, meta = ingest._route_record(record, container_index=container_index)
    assert decision == expected_decision, (
        f"router decision: expected {expected_decision!r}, got {decision!r} "
        f"with meta={meta!r}"
    )
    if expected_reason is not None:
        assert meta.get("reason") == expected_reason, (
            f"router reason: expected {expected_reason!r}, got {meta.get('reason')!r}"
        )


# ---------------------------------------------------------------------------
# translated_work routing (cases a, a', b, c)
# ---------------------------------------------------------------------------

def test_a_translated_work_cobiss_personal_routes_direct(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(a) translated_work + cobiss_personal + de/fr → direct write."""
    payload = _container_payload(work_id="container-cobiss-de-fr")
    record = _rec("translated_work", payload, provenance="cobiss_personal")

    _assert_route(record, "direct")
    _run(kg, [record], review_path, dropped_path)

    wid = "source:" + ingest._slugify(payload["work_id"])
    assert kg.G.has_node(wid), "cobiss_personal container must be written directly"

    # translated_by edge from container to translator agent
    out_relations = {
        ed.get("relation") for _u, _v, ed in kg.G.out_edges(wid, data=True)
    }
    assert "translated_by" in out_relations, (
        "direct-routed container must carry translated_by edge"
    )

    # Record must NOT appear in review queue.
    review = _load_review(review_path)
    assert payload["work_id"] not in {
        (r.get("payload") or {}).get("work_id") for r in review
    }


def test_a_prime_translated_work_curator_extra_routes_direct(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(a') translated_work + curator_extra + de/en → direct write."""
    payload = _container_payload(
        work_id="container-curator-de-en", translation_lang="en"
    )
    record = _rec("translated_work", payload, provenance="curator_extra")

    _assert_route(record, "direct")
    _run(kg, [record], review_path, dropped_path)

    wid = "source:" + ingest._slugify(payload["work_id"])
    assert kg.G.has_node(wid), "curator_extra container must be written directly"
    out_relations = {
        ed.get("relation") for _u, _v, ed in kg.G.out_edges(wid, data=True)
    }
    assert "translated_by" in out_relations

    review = _load_review(review_path)
    assert payload["work_id"] not in {
        (r.get("payload") or {}).get("work_id") for r in review
    }


def test_b_translated_work_tm_smol_routes_review(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(b) translated_work + tm_smol → review with provenance_mismatch."""
    payload = _container_payload(work_id="container-smol-de-fr")
    record = _rec("translated_work", payload, provenance="tm_smol")

    _assert_route(
        record,
        "review",
        expected_reason="provenance_mismatch_for_translated_work",
    )
    _run(kg, [record], review_path, dropped_path)

    wid = "source:" + ingest._slugify(payload["work_id"])
    assert not kg.G.has_node(wid), (
        "tm_smol translated_work must NOT be written directly"
    )

    review = _load_review(review_path)
    reasons = _route_reasons_for(review, "translated_work")
    assert "provenance_mismatch_for_translated_work" in reasons


def test_c_translated_work_doc_pair_routes_review(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(c) translated_work + doc_pair → review with provenance_mismatch."""
    payload = _container_payload(work_id="container-doc-de-fr")
    record = _rec("translated_work", payload, provenance="doc_pair")

    _assert_route(
        record,
        "review",
        expected_reason="provenance_mismatch_for_translated_work",
    )
    _run(kg, [record], review_path, dropped_path)

    wid = "source:" + ingest._slugify(payload["work_id"])
    assert not kg.G.has_node(wid), (
        "doc_pair translated_work must NOT be written directly"
    )

    review = _load_review(review_path)
    reasons = _route_reasons_for(review, "translated_work")
    assert "provenance_mismatch_for_translated_work" in reasons


# ---------------------------------------------------------------------------
# cited_work routing (cases d, e, f, g, h, j)
# ---------------------------------------------------------------------------

def test_d_cited_work_tm_smol_container_resolves_routes_direct(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(d) cited_work + tm_smol + resolvable container → direct write."""
    container = _rec(
        "translated_work",
        _container_payload(work_id="container-host"),
        provenance="cobiss_personal",
    )
    container_wid = ingest._slugify(container["payload"]["work_id"])
    cited = _rec(
        "cited_work",
        _cited_payload(
            cited_id="cited-smol-resolved",
            container_work_id=container_wid,
        ),
        provenance="tm_smol",
    )

    # Pass 3 of write_to_kg calls _route_record with the populated index.
    _assert_route(
        cited,
        "direct",
        container_index={container_wid},
    )
    _run(kg, [container, cited], review_path, dropped_path)

    cited_node = "source:" + ingest._slugify(cited["payload"]["cited_id"])
    container_node = "source:" + container_wid
    assert kg.G.has_node(cited_node), "resolved cited_work must be written"
    assert kg.G.has_edge(cited_node, container_node), (
        "cited_in edge expected when container resolves"
    )
    assert kg.G[cited_node][container_node].get("relation") == "cited_in"

    review = _load_review(review_path)
    assert cited["payload"]["cited_id"] not in {
        (r.get("payload") or {}).get("cited_id") for r in review
    }


def test_e_cited_work_doc_pair_routes_direct(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(e) cited_work + doc_pair (no container reference) → direct write."""
    cited = _rec(
        "cited_work",
        _cited_payload(cited_id="cited-doc-pair"),
        provenance="doc_pair",
    )

    _assert_route(cited, "direct")
    _run(kg, [cited], review_path, dropped_path)

    cited_node = "source:" + ingest._slugify(cited["payload"]["cited_id"])
    assert kg.G.has_node(cited_node), "doc_pair cited_work must be written directly"

    review = _load_review(review_path)
    assert cited["payload"]["cited_id"] not in {
        (r.get("payload") or {}).get("cited_id") for r in review
    }


def test_f_cited_work_cobiss_personal_routes_direct_no_cited_in(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(f) cited_work + cobiss_personal → direct write; written_by but no cited_in."""
    cited = _rec(
        "cited_work",
        _cited_payload(cited_id="self-authored-hr-sl", container_work_id=None),
        provenance="cobiss_personal",
    )

    _assert_route(cited, "direct")
    _run(kg, [cited], review_path, dropped_path)

    cited_node = "source:" + ingest._slugify(cited["payload"]["cited_id"])
    assert kg.G.has_node(cited_node), (
        "cobiss_personal cited_work must be written directly"
    )

    out_relations = {
        ed.get("relation") for _u, _v, ed in kg.G.out_edges(cited_node, data=True)
    }
    assert "written_by" in out_relations, (
        "cobiss_personal cited_work expects written_by edge to author"
    )
    assert "cited_in" not in out_relations, (
        "cobiss_personal cited_work must NOT carry cited_in (self-authored)"
    )


def test_g_cited_work_container_unresolved_routes_review(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(g) cited_work + valid provenance + container_work_id NOT in index → review."""
    cited = _rec(
        "cited_work",
        _cited_payload(
            cited_id="cited-unresolved",
            container_work_id="container-never-written",
        ),
        provenance="tm_smol",
    )

    # Empty container_index simulates pass-3 not finding the container.
    _assert_route(
        cited,
        "review",
        container_index=set(),
        expected_reason="container_not_found",
    )
    _run(kg, [cited], review_path, dropped_path)

    cited_node = "source:" + ingest._slugify(cited["payload"]["cited_id"])
    assert not kg.G.has_node(cited_node), (
        "cited_work with unresolved container must NOT be written"
    )

    review = _load_review(review_path)
    reasons = _route_reasons_for(review, "cited_work")
    assert "container_not_found" in reasons


def test_h_cited_work_provenance_absent_routes_review(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(h) cited_work + provenance absent → review with provenance_missing_or_unknown."""
    cited = _rec(
        "cited_work",
        _cited_payload(cited_id="cited-no-provenance"),
        # provenance="__unset__" sentinel keeps `provenance` out of source.
    )

    _assert_route(
        cited,
        "review",
        expected_reason="provenance_missing_or_unknown",
    )
    _run(kg, [cited], review_path, dropped_path)

    cited_node = "source:" + ingest._slugify(cited["payload"]["cited_id"])
    assert not kg.G.has_node(cited_node), (
        "cited_work without provenance must NOT be written"
    )

    review = _load_review(review_path)
    reasons = _route_reasons_for(review, "cited_work")
    assert "provenance_missing_or_unknown" in reasons


def test_j_cited_work_language_pair_undetermined_routes_review(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(j) cited_work + valid provenance + both orig_lang & translation_lang None → review."""
    cited = _rec(
        "cited_work",
        _cited_payload(
            cited_id="cited-no-lang-pair",
            orig_lang=None,
            translation_lang=None,
        ),
        provenance="tm_smol",
    )

    _assert_route(
        cited,
        "review",
        expected_reason="language_pair_undetermined",
    )
    _run(kg, [cited], review_path, dropped_path)

    cited_node = "source:" + ingest._slugify(cited["payload"]["cited_id"])
    assert not kg.G.has_node(cited_node), (
        "cited_work with undetermined language pair must NOT be written"
    )

    review = _load_review(review_path)
    reasons = _route_reasons_for(review, "cited_work")
    assert "language_pair_undetermined" in reasons


# ---------------------------------------------------------------------------
# agent_person routing (case i)
# ---------------------------------------------------------------------------

def test_i_agent_person_tm_smol_routes_direct(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(i) agent_person + tm_smol → direct write."""
    agent = _rec(
        "agent_person",
        _agent_payload(name="Marko Kovač", dedup_group="m.kovac"),
        provenance="tm_smol",
    )

    _assert_route(agent, "direct")
    _run(kg, [agent], review_path, dropped_path)

    agent_node = "agent:" + ingest._slugify(agent["payload"]["name"])
    assert kg.G.has_node(agent_node), (
        "agent_person with tm_smol provenance must be written directly"
    )

    review = _load_review(review_path)
    assert agent["payload"]["name"] not in {
        (r.get("payload") or {}).get("name") for r in review
    }


def test_i_agent_person_unknown_source_routes_review(
    kg: KnowledgeGraph, review_path: Path, dropped_path: Path
) -> None:
    """(i) agent_person + unknown_source → review with provenance_missing_or_unknown."""
    agent = _rec(
        "agent_person",
        _agent_payload(name="Petra Hribar", dedup_group="p.hribar"),
        provenance="unknown_source",
    )

    _assert_route(
        agent,
        "review",
        expected_reason="provenance_missing_or_unknown",
    )
    _run(kg, [agent], review_path, dropped_path)

    agent_node = "agent:" + ingest._slugify(agent["payload"]["name"])
    assert not kg.G.has_node(agent_node), (
        "agent_person with unknown provenance must NOT be written directly"
    )

    review = _load_review(review_path)
    reasons = _route_reasons_for(review, "agent_person")
    assert "provenance_missing_or_unknown" in reasons
