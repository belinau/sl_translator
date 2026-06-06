"""Ontology-audit compliance tests for the typed-vl unit.

One assertion per repair in data/ontology_audit/typed-vl.md. The test runs
against an in-memory pipeline — never reads / writes data/knowledge.db.
"""

from __future__ import annotations

import pytest

from translate_core.entity_extraction.citation_types import (
    CitationStyle,
    CitationType,
)
from translate_core.entity_extraction.vl_typed_extractor import (
    extract_typed_citation,
    records_from_verified,
)


# ── Stubs ─────────────────────────────────────────────────────────────────────


class _RaisingVL:
    """VL extractor stand-in whose ._vl_chat must NEVER be called."""

    def _vl_chat(self, *args, **kwargs):  # noqa: D401
        raise AssertionError(
            "_vl_chat must not be called — multi-prose segment should "
            "short-circuit before any VL call",
        )


class _SingleCitationVL:
    """VL extractor stand-in that returns canned responses for Call 1 + Call 2.

    Records the calls it received so the test can assert the VL pipeline
    actually ran (i.e. extract_typed_citation did NOT short-circuit).
    """

    def __init__(self):
        self.calls: list[dict] = []

    def _vl_chat(self, *, system, user, prefill, max_tokens):
        self.calls.append({"system": system, "prefill": prefill})
        if "Classify" in system:
            return '{"type":"book"}'
        # Call 2 — minimal valid book extraction copied verbatim from the segment.
        return (
            '{"authors":[{"surname":"Foucault","given_name":"Michel"}],'
            '"title":"Surveiller et punir","subtitle":null,"translator":null,'
            '"editor":null,"edition":null,"publisher":"Gallimard",'
            '"city":"Paris","year":1975,"pages":"215","url":null,"doi":null}'
        )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_citation_type_other_value_is_cited_work():
    """Audit repair: CitationType.OTHER.value renamed from 'other' to
    'cited_work' to match §2.4.1's legacy-generic name."""
    assert CitationType.OTHER.value == "cited_work"


def test_records_from_verified_omits_unknown_citation_style():
    """Audit repair §4: when verified['style'] is missing or 'unknown',
    'citation_style' must NOT appear on the cited_work payload (§4
    invariant 6 — citation_style must name a defined style)."""
    verified = {
        "type": "book",
        "style": "unknown",
        "authors": [{"surname": "Foucault", "given_name": "Michel"}],
        "title": "Surveiller et punir",
        "year": 1975,
    }
    records = records_from_verified(
        verified,
        origin="probe.tmx",
        seg_idx=0,
        src="Foucault, Michel. 1975. Surveiller et punir.",
        tgt="",
    )
    assert records, "expected at least one record"
    cited = next(r for r in records if r["kind"] == "cited_work")
    # The field is either omitted entirely OR carries a real style label.
    style_val = cited["payload"].get("citation_style")
    assert style_val != "unknown"
    assert style_val != CitationStyle.UNKNOWN.value
    assert "citation_style" not in cited["payload"]


def test_extract_typed_citation_refuses_multi_title_prose():
    """Audit repair §5 (Grom→Sujets fix): a single segment that packs ≥2
    italicised titles AND ≥2 years AND >200 chars is multi-citation prose,
    not a single citation. extract_typed_citation must short-circuit to
    None BEFORE touching the VL client."""
    sujets_segment = (
        "Choreographic works ... include choreographies by "
        "**Sylvain Huc** *Sujets* (2018) and **Sanja Nesko Persin** "
        "*The Moment Before* (2024), as well as **Tomaz Grom**'s "
        "experimental film *Don't Think It Will Ever Pass* (2023) "
        "and **Jefta van Dinther**'s extraordinary choreo-vocal work "
        "*Unearth* (2022)."
    )
    # >200 chars precondition for the refusal
    assert len(sujets_segment) > 200

    raising_vl = _RaisingVL()
    # _RaisingVL._vl_chat would raise if called — assertion succeeds only
    # when the early-return path is taken.
    result = extract_typed_citation(raising_vl, sujets_segment)
    assert result is None


def test_extract_typed_citation_processes_single_citation_normally():
    """Audit repair §5: a normal single-citation segment must NOT trigger
    the multi-prose refusal — it must proceed through the VL pipeline."""
    segment = (
        "Foucault, Michel. 1975. Surveiller et punir. "
        "Paris: Gallimard, p. 215."
    )
    vl = _SingleCitationVL()
    result = extract_typed_citation(vl, segment)
    # Must have invoked the classify call at minimum (proves no short-circuit).
    assert any("Classify" in c["system"] for c in vl.calls), (
        "classify call missing — extractor short-circuited on a normal citation"
    )
    # Verifier should accept the canned book extraction (year + publisher
    # + author surname all substring-present in the segment).
    assert result is not None
    assert result.get("type") == CitationType.BOOK.value


def test_records_from_verified_routes_unverified_other_to_review():
    """Audit repair §3: when the verifier rejects and the fallback_other
    record is built, records_from_verified must emit a routable record
    (cited_work with sparse signals so the existing < 0.85 tier sends it
    to data/extraction_review.json) — NOT drop it on the floor."""
    fallback = {
        "type": CitationType.OTHER.value,  # 'cited_work'
        "style": CitationStyle.CHICAGO_EN.value,
        "authors": [{"surname": "Unknown", "given_name": "Author"}],
        "title": "Probably Not A Real Title",
        "raw_text": "raw segment text here",
        "notes": "verifier rejected typed extraction",
    }
    records = records_from_verified(
        fallback,
        origin="probe.tmx",
        seg_idx=7,
        src="src text",
        tgt="",
    )
    assert records, "OTHER branch must emit a record, not return []"
    rec = records[0]
    assert rec["kind"] == "cited_work"
    # Sparse signals — verified_from_text MUST NOT be set, otherwise the
    # scorer would tier this to DIRECT_WRITE and bypass curator review.
    assert rec["signals"].get("verified_from_text") is not True
    assert rec["payload"]["project_type"] == CitationType.OTHER.value


def test_records_from_verified_other_with_unknown_style_still_omits_field():
    """Combined: an OTHER-branch record built from a fallback whose style
    is 'unknown' must NOT carry citation_style='unknown'."""
    fallback = {
        "type": CitationType.OTHER.value,
        "style": "unknown",
        "authors": [{"surname": "Doe"}],
        "title": "Untitled",
    }
    records = records_from_verified(
        fallback,
        origin="probe.tmx",
        seg_idx=0,
        src="",
        tgt="",
    )
    assert records
    assert "citation_style" not in records[0]["payload"]


def test_system_extract_typed_dead_symbol_removed():
    """Audit dead-code §6: SYSTEM_EXTRACT_TYPED has no caller and was deleted."""
    from translate_core.entity_extraction import citation_types as ct

    assert not hasattr(ct, "SYSTEM_EXTRACT_TYPED"), (
        "SYSTEM_EXTRACT_TYPED was flagged dead and should have been removed"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
