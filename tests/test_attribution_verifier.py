"""Tests for the three-layer concept attribution verifier.

Covers: textual grounding (pass/fail), roster confirmation, roster
mismatch, the second model pass (yes/no/unavailable), roster fallback,
and the verify_records batch helper.
"""
from __future__ import annotations

from translate_core.entity_extraction.attribution_verifier import (
    verify_concept_attribution,
    verify_records,
)


def _concept_record(
    *,
    concept_id: str,
    label: str,
    author: str,
    evidence_span: str | None,
    roster_confirmed: bool = False,
) -> dict:
    return {
        "kind": "concept",
        "payload": {
            "concept_id": concept_id,
            "label": label,
            "label_orig": label,
            "originating_author": author,
            "evidence_span": evidence_span,
        },
        "signals": {"attribution_roster_confirmed": roster_confirmed},
    }


# ── Layer 1: textual grounding ───────────────────────────────────────────────

def test_grounded_real_span_verifies_via_model():
    """Span is a real substring containing author + concept; concept not in
    the roster → layer 3 model pass confirms."""
    src = "As Karen Barad argues, agential realism dissolves the cut."
    rec = _concept_record(
        concept_id="concept:agential-realism",
        label="agential realism",
        author="Karen Barad",
        evidence_span="Karen Barad argues, agential realism",
    )
    res = verify_concept_attribution(
        rec, src, "", model_verify=lambda *a: True,
    )
    assert res["grounded"] is True
    assert res["roster_confirmed"] is False
    assert res["model_verified"] is True
    assert res["verified"] is True
    assert rec["signals"]["attribution_verified"] is True


def test_fabricated_span_is_rejected():
    """Span does not occur in the segment → not grounded → not verified,
    and the model pass is never spent."""
    called = {"n": 0}

    def _mv(*a):
        called["n"] += 1
        return True

    src = "Some unrelated text about dance and movement."
    rec = _concept_record(
        concept_id="concept:agential-realism",
        label="agential realism",
        author="Karen Barad",
        evidence_span="Karen Barad's agential realism framework",
    )
    res = verify_concept_attribution(rec, src, "", model_verify=_mv)
    assert res["grounded"] is False
    assert res["verified"] is False
    assert called["n"] == 0  # no model call when grounding fails
    assert rec["signals"]["attribution_verified"] is False


def test_span_missing_author_name_is_rejected():
    """Span is real but does not contain the author → not grounded."""
    src = "Foucault's notion of biopower governs populations."
    rec = _concept_record(
        concept_id="concept:biopower",
        label="biopower",
        author="Michel Foucault",
        evidence_span="notion of biopower governs populations",  # no author
    )
    res = verify_concept_attribution(rec, src, "", model_verify=lambda *a: True)
    assert res["grounded"] is False
    assert res["verified"] is False


def test_span_missing_concept_label_is_rejected():
    src = "Foucault's notion of biopower governs populations."
    rec = _concept_record(
        concept_id="concept:biopower",
        label="biopower",
        author="Michel Foucault",
        evidence_span="Foucault's notion governs populations",  # no concept
    )
    res = verify_concept_attribution(rec, src, "", model_verify=lambda *a: True)
    assert res["grounded"] is False
    assert res["verified"] is False


def test_grounded_in_target_text():
    """Grounding accepts a span from the TARGET side too."""
    tgt = "Foucaultova ideja biomoči ureja populacije."
    rec = _concept_record(
        concept_id="concept:biopower",
        label="biomoči",
        author="Foucault",
        evidence_span="Foucaultova ideja biomoči",
    )
    res = verify_concept_attribution(rec, "", tgt, model_verify=lambda *a: True)
    assert res["grounded"] is True
    assert res["verified"] is True


# ── Layer 2: roster cross-check ──────────────────────────────────────────────

def test_roster_confirmed_skips_model_pass():
    """Concept in the roster + matching author → confirmed, no model call."""
    # concept:empowerment → "bell hooks" in data/concept_theorists.json
    called = {"n": 0}

    def _mv(*a):
        called["n"] += 1
        return False  # would reject if called

    src = "bell hooks redefines empowerment as a practice of freedom."
    rec = _concept_record(
        concept_id="concept:empowerment",
        label="empowerment",
        author="bell hooks",
        evidence_span="bell hooks redefines empowerment",
    )
    res = verify_concept_attribution(rec, src, "", model_verify=_mv)
    assert res["grounded"] is True
    assert res["roster_confirmed"] is True
    assert res["verified"] is True
    assert called["n"] == 0


def test_roster_mismatch_is_rejected_without_model_call():
    """Concept in the roster but attributed to the wrong author → not
    verified, and no model call is spent defending the conflict."""
    called = {"n": 0}

    def _mv(*a):
        called["n"] += 1
        return True

    src = "John Doe redefines empowerment as a practice of freedom."
    rec = _concept_record(
        concept_id="concept:empowerment",
        label="empowerment",
        author="John Doe",
        evidence_span="John Doe redefines empowerment",
    )
    res = verify_concept_attribution(rec, src, "", model_verify=_mv)
    assert res["grounded"] is True
    assert res["roster_confirmed"] is False
    assert res["roster_mismatch"] is True
    assert res["verified"] is False
    assert called["n"] == 0


# ── Layer 3: second model pass ───────────────────────────────────────────────

def test_unrostered_model_rejected_is_not_verified():
    src = "Jane Roe's framework of pluriversal design rethinks worlds."
    rec = _concept_record(
        concept_id="concept:pluriversal-design",
        label="pluriversal design",
        author="Jane Roe",
        evidence_span="Jane Roe's framework of pluriversal design",
    )
    res = verify_concept_attribution(rec, src, "", model_verify=lambda *a: False)
    assert res["grounded"] is True
    assert res["model_verified"] is False
    assert res["verified"] is False


def test_unrostered_model_unavailable_is_not_verified():
    """Model endpoint down → None → not verified (queued for review, not
    auto-written)."""
    src = "Jane Roe's framework of pluriversal design rethinks worlds."
    rec = _concept_record(
        concept_id="concept:pluriversal-design",
        label="pluriversal design",
        author="Jane Roe",
        evidence_span="Jane Roe's framework of pluriversal design",
    )
    res = verify_concept_attribution(rec, src, "", model_verify=lambda *a: None)
    assert res["model_verified"] is None
    assert res["verified"] is False


def test_model_verify_exception_does_not_crash():
    src = "Jane Roe's framework of pluriversal design rethinks worlds."
    rec = _concept_record(
        concept_id="concept:pluriversal-design",
        label="pluriversal design",
        author="Jane Roe",
        evidence_span="Jane Roe's framework of pluriversal design",
    )

    def _boom(*a):
        raise RuntimeError("network down")

    res = verify_concept_attribution(rec, src, "", model_verify=_boom)
    assert res["verified"] is False
    assert rec["signals"]["attribution_verified"] is False


# ── Roster fallback ──────────────────────────────────────────────────────────

def test_roster_fallback_verifies_without_evidence_span():
    """A roster-backed attribution (filled by _build_concept, no evidence
    span) is authority-confirmed by construction → verified directly."""
    rec = _concept_record(
        concept_id="concept:empowerment",
        label="empowerment",
        author="bell hooks",
        evidence_span=None,
        roster_confirmed=True,
    )
    res = verify_concept_attribution(rec, "x", "y", model_verify=lambda *a: True)
    assert res["verified"] is True
    assert res["reason"] == "roster_fallback"


# ── verify_records batch helper ──────────────────────────────────────────────

def test_verify_records_skips_non_concept_and_no_author():
    src = "Some segment text."
    agent_rec = {"kind": "agent_person", "payload": {"name": "Foo"},
                 "signals": {}}
    no_author_concept = {
        "kind": "concept",
        "payload": {"concept_id": "concept:x", "label": "x",
                    "originating_author": None},
        "signals": {},
    }
    verify_records([agent_rec, no_author_concept], src, "",
                   model_verify=lambda *a: True)
    assert agent_rec["signals"] == {}  # untouched
    assert no_author_concept["signals"]["attribution_verified"] is False