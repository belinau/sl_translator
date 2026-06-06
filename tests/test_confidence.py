"""Phase 3 (TDD red) — failing tests for the confidence-scoring rewrite.

Motivation (ontology.md §4 invariant 9, audit §3.4, §10.10):
the current implementation of `confidence.py` adds a universal `+0.60` for
`smol_verified_classification`, which floors every smol record at ≥0.85 and
bypasses the review queue. Phase 3 replaces that single universal bump with:

  - `smol_extracted`                    →  +0.15 (unchanged)
  - `verified_from_text`                →  +0.10 (unchanged)
  - `smol_verified_classification`      →   REMOVED as a universal bump
                                            (the flag may still appear in
                                             `signals` for informational use
                                             but it must NOT add to the score
                                             and MUST NOT appear in
                                             `reason_codes`)

  - Composite gate: count truthy signals among
        {title_bilingual, container_attached, project_type_typed}
    (with `has_bilingual_title` accepted as an alias for `title_bilingual`).
    If ≥2 of 3 are truthy, add a SINGLE `+0.30` bump with reason code
    `"composite_2of3"`. Otherwise no bump.

  - Curator endorsement: `curator_endorsed=True` → `+0.40`
    (reason code `"curator_endorsed"`), independent of the composite gate.

These tests target the public API `score_record(record_kind, signals)` and the
public constants `THRESHOLD_DIRECT` / `THRESHOLD_REVIEW`.

Every test in this module is designed to FAIL against the implementation as
of the start of Phase 3.1 — that is the point of the TDD red phase.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.entity_extraction.confidence import (
    ConfidenceTier,
    THRESHOLD_DIRECT,
    THRESHOLD_REVIEW,
    score_record,
)


# Tolerance for floating-point comparisons of confidence scores. The scorer
# does plain float arithmetic so 1e-9 is plenty.
EPS = 1e-9


# ---------------------------------------------------------------------------
# Public-constant sanity tests
# ---------------------------------------------------------------------------


def test_thresholds_unchanged():
    """Phase 3 must not move the public thresholds."""
    assert THRESHOLD_DIRECT == pytest.approx(0.85, abs=EPS)
    assert THRESHOLD_REVIEW == pytest.approx(0.55, abs=EPS)


# ---------------------------------------------------------------------------
# A. smol_verified_classification is no longer a universal bump
# ---------------------------------------------------------------------------


def test_smol_verified_classification_is_not_a_universal_bump():
    """A smol record carrying only the three "universal" flags — and none of
    the composite signals nor any per-kind structural signals — must score at
    the floor of `base + smol_extracted + verified_from_text` = 0.55 and route
    to REVIEW, NOT DIRECT_WRITE.

    Against the current implementation this scores 0.30 + 0.60 + 0.15 + 0.10
    = 1.15 → clamped to 1.0 → DIRECT_WRITE. After Phase 3 it must be 0.55
    REVIEW exactly.
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "smol_verified_classification": True,
            # No has_title / has_author / has_year / has_publisher etc., so
            # the per-kind branch contributes nothing.
        },
    )

    assert result.confidence == pytest.approx(0.55, abs=EPS), (
        f"expected 0.55 (base 0.30 + smol_extracted 0.15 + verified_from_text "
        f"0.10), got {result.confidence}"
    )
    assert result.tier == ConfidenceTier.REVIEW
    assert "smol_verified_classification" not in result.reason_codes, (
        "smol_verified_classification must no longer contribute a reason code "
        "after Phase 3 — it is an informational flag only"
    )
    assert "smol_extracted" in result.reason_codes
    assert "verified_from_text" in result.reason_codes


def test_agent_person_smol_only_signals_routes_to_review():
    """Companion to test A for `record_kind=agent_person`: with only the three
    universal flags and none of the per-kind structural signals
    (plausible_person_name, multi_mention, role_attribution_context, etc.),
    the record must land at 0.55 REVIEW.

    Currently it scores 1.0 DIRECT_WRITE because of the +0.60 bump.
    """
    result = score_record(
        "agent_person",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "smol_verified_classification": True,
        },
    )

    assert result.confidence == pytest.approx(0.55, abs=EPS)
    assert result.tier == ConfidenceTier.REVIEW
    assert "smol_verified_classification" not in result.reason_codes


# ---------------------------------------------------------------------------
# B. Composite 2-of-3 with all three signals lands at DIRECT_WRITE
# ---------------------------------------------------------------------------


def test_composite_2of3_lands_at_direct_write():
    """All three composite signals → single +0.30 bump → 0.85 DIRECT_WRITE.

    Score = base 0.30 + smol_extracted 0.15 + verified_from_text 0.10
          + composite_2of3 0.30
          = 0.85.

    The composite must contribute a SINGLE reason code `composite_2of3`,
    not three separate ones (`title_bilingual`, `container_attached`,
    `project_type_typed`).
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "title_bilingual": True,
            "container_attached": True,
            "project_type_typed": True,
        },
    )

    assert result.confidence == pytest.approx(0.85, abs=EPS), (
        f"expected exactly 0.85 (base+smol+verified+composite), got "
        f"{result.confidence}"
    )
    assert result.tier == ConfidenceTier.DIRECT_WRITE
    assert result.reason_codes.count("composite_2of3") == 1, (
        f"composite_2of3 must appear exactly once, got reasons={result.reason_codes}"
    )


# ---------------------------------------------------------------------------
# C. Exactly two composite signals also triggers the bump
# ---------------------------------------------------------------------------


def test_composite_exactly_two_of_three_still_triggers():
    """Two truthy of {title_bilingual, container_attached, project_type_typed}
    is enough to trigger the +0.30 composite bump and reach DIRECT_WRITE.
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "title_bilingual": True,
            "container_attached": True,
            # project_type_typed deliberately absent
        },
    )

    assert result.confidence == pytest.approx(0.85, abs=EPS)
    assert result.tier == ConfidenceTier.DIRECT_WRITE
    assert "composite_2of3" in result.reason_codes
    assert result.reason_codes.count("composite_2of3") == 1


# ---------------------------------------------------------------------------
# D. One-of-three does NOT trigger the composite
# ---------------------------------------------------------------------------


def test_composite_one_of_three_does_not_trigger():
    """Only one truthy of {title_bilingual, container_attached, project_type_typed}
    is insufficient. The record stays at the universal-only floor of 0.55 and
    routes to REVIEW.
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "title_bilingual": True,
            # container_attached, project_type_typed deliberately absent
        },
    )

    assert result.confidence == pytest.approx(0.55, abs=EPS), (
        f"expected 0.55 floor (no composite bump for 1-of-3), got "
        f"{result.confidence}"
    )
    assert result.tier == ConfidenceTier.REVIEW
    assert "composite_2of3" not in result.reason_codes


# ---------------------------------------------------------------------------
# E. `has_bilingual_title` aliases `title_bilingual` inside the composite
# ---------------------------------------------------------------------------


def test_has_bilingual_title_aliases_title_bilingual_in_composite():
    """Existing smol builders set `has_bilingual_title` (see
    `_build_cited_work` at smol_extractor.py:563), not `title_bilingual`.
    The Phase 3 composite gate must accept `has_bilingual_title` as an alias
    so the existing builders' output isn't silently demoted to REVIEW.

    With `has_bilingual_title=True` + `container_attached=True` (2 of 3
    truthy), the composite bump applies → 0.85 DIRECT_WRITE.
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "has_bilingual_title": True,  # alias for title_bilingual
            "container_attached": True,
        },
    )

    assert result.confidence == pytest.approx(0.85, abs=EPS), (
        f"has_bilingual_title must count toward the 2-of-3 composite; "
        f"expected 0.85, got {result.confidence}"
    )
    assert result.tier == ConfidenceTier.DIRECT_WRITE
    assert "composite_2of3" in result.reason_codes


def test_composite_does_not_double_count_bilingual_aliases():
    """Edge case: if BOTH `title_bilingual` and `has_bilingual_title` are set
    (some upstream code paths set both for safety), they must count as a
    SINGLE composite signal — not two — so the composite bump is awarded
    exactly once even on a record with just `title_bilingual` +
    `has_bilingual_title` (which is really 1 of 3, not 2 of 3).
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "title_bilingual": True,
            "has_bilingual_title": True,
            # container_attached, project_type_typed deliberately absent
        },
    )

    # Only one underlying composite signal (bilingual title) is truthy.
    # That's 1 of 3, so no composite bump. Score must stay at 0.55 floor.
    assert result.confidence == pytest.approx(0.55, abs=EPS), (
        f"title_bilingual + has_bilingual_title is one signal, not two; "
        f"expected 0.55 REVIEW floor, got {result.confidence}"
    )
    assert result.tier == ConfidenceTier.REVIEW
    assert "composite_2of3" not in result.reason_codes


# ---------------------------------------------------------------------------
# F. curator_endorsed contributes +0.40 on its own
# ---------------------------------------------------------------------------


def test_curator_endorsed_is_plus_0_40():
    """`curator_endorsed=True` contributes +0.40 with reason code
    `curator_endorsed`, independent of the composite gate.

    Score = base 0.30 + smol_extracted 0.15 + verified_from_text 0.10
          + curator_endorsed 0.40
          = 0.95 → DIRECT_WRITE.
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "curator_endorsed": True,
            # No composite signals.
        },
    )

    assert result.confidence == pytest.approx(0.95, abs=EPS), (
        f"expected 0.95 (base+smol+verified+curator), got {result.confidence}"
    )
    assert result.tier == ConfidenceTier.DIRECT_WRITE
    assert "curator_endorsed" in result.reason_codes
    assert "composite_2of3" not in result.reason_codes


# ---------------------------------------------------------------------------
# G. curator + full composite stack independently, clamp at 1.0
# ---------------------------------------------------------------------------


def test_curator_endorsed_and_composite_stack_without_double_count():
    """Full composite (3/3 truthy) + curator endorsement: bumps applied
    independently. Raw sum would be 0.30 + 0.15 + 0.10 + 0.30 + 0.40 = 1.25,
    which the scorer clamps to 1.0. Both reason codes must appear, each
    exactly once.
    """
    result = score_record(
        "cited_work",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "title_bilingual": True,
            "container_attached": True,
            "project_type_typed": True,
            "curator_endorsed": True,
        },
    )

    assert result.confidence == pytest.approx(1.0, abs=EPS), (
        f"expected clamp to 1.0, got {result.confidence}"
    )
    assert result.tier == ConfidenceTier.DIRECT_WRITE
    assert "composite_2of3" in result.reason_codes
    assert "curator_endorsed" in result.reason_codes
    assert result.reason_codes.count("composite_2of3") == 1
    assert result.reason_codes.count("curator_endorsed") == 1


# ---------------------------------------------------------------------------
# H. Per-record-kind bumps are unchanged for agent_person
# ---------------------------------------------------------------------------


def test_per_kind_bumps_unchanged_for_agent_person():
    """Phase 3 must not touch the per-record-kind bumps that begin around
    confidence.py:80. A realistic agent_person signal set (plausible person
    name, multi-mention, role attribution, bilingual name) must continue to
    receive its full per-kind credit:

        +0.25 plausible_person_name
        +0.15 multi_mention
        +0.10 role_attribution_context
        +0.10 bilingual_name

    Combined with the universal bumps after Phase 3:

        base 0.30
      + smol_extracted 0.15
      + verified_from_text 0.10
      + per-kind 0.60
      = 1.15 → clamped to 1.0 → DIRECT_WRITE

    `smol_verified_classification=True` is included in the input signals
    (smol always sets it) but must NOT appear in the reason codes after
    Phase 3.

    Against the current implementation the score clamps to 1.0 too, but the
    reason codes include `smol_verified_classification`, which is the
    discriminator that makes this test fail today.
    """
    result = score_record(
        "agent_person",
        {
            "smol_extracted": True,
            "verified_from_text": True,
            "smol_verified_classification": True,
            "plausible_person_name": True,
            "multi_mention": True,
            "role_attribution_context": True,
            "bilingual_name": True,
        },
    )

    # The per-kind bumps must remain fully credited and the score must
    # clamp to 1.0.
    assert result.confidence == pytest.approx(1.0, abs=EPS)
    assert result.tier == ConfidenceTier.DIRECT_WRITE

    # The per-kind reason codes must all be present (per-kind branch
    # unchanged by Phase 3).
    for code in (
        "plausible_person_name",
        "multi_mention",
        "role_attribution_context",
        "bilingual_name",
    ):
        assert code in result.reason_codes, f"missing per-kind code {code!r}"

    # And the disallowed universal bump must NOT be in the reason codes.
    assert "smol_verified_classification" not in result.reason_codes, (
        "smol_verified_classification must no longer contribute a reason "
        "code after Phase 3"
    )
