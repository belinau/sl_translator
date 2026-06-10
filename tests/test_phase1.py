# tests/test_phase1.py
#
# Unit tests for Phase 1 changes:
# - confidence.py: verified_typed_pipeline, verified_from_text, multi_mention reduction
# - citation_types.py: detect_author_form style-scoped heuristic
# - _slug.py: type-free work IDs (slugify behaviour previously lived in vl_typed_extractor)

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.entity_extraction.confidence import score_record, THRESHOLD_DIRECT
from translate_core.entity_extraction.citation_types import (
    CitationStyle,
    detect_author_form,
)
from translate_core.entity_extraction._slug import _slugify


# ======================================================================
# Tests: confidence.py — verified_typed_pipeline signal
# ======================================================================


class TestVerifiedTypedPipelineSignal:
    """Phase 1: verified_typed_pipeline (+0.15) applies to typed citation kinds and agent_person."""

    TYPED_KINDS = {
        "book", "book_chapter", "journal_article", "magazine_article",
        "newspaper_article", "web_source", "exhibition_catalog",
        "interview", "thesis_dissertation",
    }

    def test_typed_kinds_get_bonus(self):
        """All typed citation kinds should receive +0.15 for verified_typed_pipeline."""
        for kind in self.TYPED_KINDS:
            result = score_record(kind, {"verified_typed_pipeline": True, "has_title": True})
            assert "verified_typed_pipeline" in result.reason_codes, f"{kind} missing signal"

    def test_agent_person_gets_bonus(self):
        """agent_person should receive +0.15 for verified_typed_pipeline."""
        result = score_record("agent_person", {
            "plausible_person_name": True,
            "verified_typed_pipeline": True,
        })
        assert "verified_typed_pipeline" in result.reason_codes

    def test_generic_cited_work_no_bonus(self):
        """cited_work (legacy generic path) should NOT get verified_typed_pipeline."""
        result = score_record("cited_work", {
            "has_author": True,
            "has_title": True,
            "verified_typed_pipeline": True,
        })
        assert "verified_typed_pipeline" not in result.reason_codes

    def test_institution_no_bonus(self):
        """institution should NOT get verified_typed_pipeline."""
        result = score_record("institution", {
            "known_publisher": True,
            "verified_typed_pipeline": True,
        })
        assert "verified_typed_pipeline" not in result.reason_codes

    def test_artwork_no_bonus(self):
        """artwork should NOT get verified_typed_pipeline."""
        result = score_record("artwork", {
            "has_title": True,
            "verified_typed_pipeline": True,
        })
        assert "verified_typed_pipeline" not in result.reason_codes

    def test_artist_no_bonus(self):
        """artist should NOT get verified_typed_pipeline."""
        result = score_record("artist", {
            "plausible_person_name": True,
            "verified_typed_pipeline": True,
        })
        assert "verified_typed_pipeline" not in result.reason_codes


# ======================================================================
# Tests: confidence.py — verified_from_text for agent_person
# ======================================================================


class TestVerifiedFromTextAgentPerson:
    """Phase 1: verified_from_text (+0.10) for agent_person branch."""

    def test_agent_person_verified_from_text(self):
        result = score_record("agent_person", {
            "plausible_person_name": True,
            "verified_from_text": True,
        })
        assert "verified_from_text" in result.reason_codes

    def test_book_still_gets_verified_from_text(self):
        """Book typed citation should also still get verified_from_text."""
        result = score_record("book", {
            "has_author": True,
            "verified_from_text": True,
        })
        assert "verified_from_text" in result.reason_codes

    def test_agent_person_without_verified_from_text(self):
        """Agent from generic path should still score below threshold."""
        result = score_record("agent_person", {
            "plausible_person_name": True,
            "role_attribution_context": True,
        })
        # base 0.30 + plausible 0.25 + role 0.10 = 0.65
        assert result.confidence < THRESHOLD_DIRECT


# ======================================================================
# Tests: confidence.py — multi_mention reduced for agent_person
# ======================================================================


class TestMultiMentionReduction:
    """Phase 1: multi_mention reduced from 0.20 to 0.15 for agent_person."""

    def test_multi_mention_agent_person(self):
        result = score_record("agent_person", {"multi_mention": True})
        # base 0.30 + multi_mention 0.15 = 0.45
        assert result.confidence == pytest.approx(0.45, abs=0.01)
        assert "multi_mention" in result.reason_codes

    def test_multi_mention_not_applied_to_cited_work(self):
        """cited_work branch has its own multi_mention handling — verify no regression."""
        result = score_record("cited_work", {"multi_mention": True})
        # cited_work doesn't have multi_mention signal
        assert "multi_mention" not in result.reason_codes


# ======================================================================
# Tests: detect_author_form — style-scoped heuristic
# ======================================================================


class TestDetectAuthorForm:
    """Phase 1: detect_author_form uses style-scoped heuristic instead of always returning 'footnote'."""

    def test_footnote_form_default(self):
        """No style → default to footnote."""
        assert detect_author_form("Some text") == "footnote"

    def test_chicago_sl_footnote(self):
        """Chicago SL footnote (firstname-last) → footnote."""
        assert detect_author_form(
            "Alenka Šelih navaja, da je pravo pomembno.",
            style=CitationStyle.CHICAGO_SL,
        ) == "footnote"

    def test_chicago_en_footnote(self):
        """Chicago EN footnote (firstname-last) → footnote."""
        assert detect_author_form(
            "Donna Haraway argues that...",
            style=CitationStyle.CHICAGO_EN,
        ) == "footnote"

    def test_chicago_sl_bibliography(self):
        """Chicago SL bibliography (lastname-first) → bibliography."""
        assert detect_author_form(
            "Šelih, Alenka. Pravna zgodovina.",
            style=CitationStyle.CHICAGO_SL,
        ) == "bibliography"

    def test_chicago_en_bibliography(self):
        """Chicago EN bibliography (lastname-first) → bibliography."""
        assert detect_author_form(
            "Haraway, Donna. A Cyborg Manifesto.",
            style=CitationStyle.CHICAGO_EN,
        ) == "bibliography"

    def test_unknown_style_defaults_footnote(self):
        """UNKNOWN style always defaults to footnote regardless of text pattern."""
        assert detect_author_form(
            "Haraway, Donna. A Cyborg Manifesto.",
            style=CitationStyle.UNKNOWN,
        ) == "footnote"

    def test_none_style_defaults_footnote(self):
        """None style always defaults to footnote."""
        assert detect_author_form(
            "Haraway, Donna. A Cyborg Manifesto.",
            style=None,
        ) == "footnote"

    def test_slovenian_bibliography(self):
        """Slovenian characters in bibliography form."""
        """Slovenian characters in bibliography form."""
        assert detect_author_form(
            "Založnik, Jasmina. Zavzemanje prostorov.",
            style=CitationStyle.CHICAGO_SL,
        ) == "bibliography"

    def test_numbered_bibliography_entry_is_footnote(self):
        """Numbered bibliography entries (e.g. '1. Haraway, Donna.') start with
        a digit, so the regex doesn't match — they fall through to footnote.
        This is acceptable; numbered entries are footnotes in Chicago."""
        assert detect_author_form(
            "1. Haraway, Donna. A Cyborg Manifesto.",
            style=CitationStyle.CHICAGO_EN,
        ) == "footnote"

    def test_lowercase_start_not_bibliography(self):
        """Lowercase start should not match bibliography pattern."""
        assert detect_author_form(
            "lowercase start text",
            style=CitationStyle.CHICAGO_SL,
        ) == "footnote"

# ======================================================================
# Tests: _slug — type-free work IDs
# ======================================================================


class TestTypeFreeWorkIDs:
    """Phase 1: Work IDs no longer include citation type prefix."""

    def test_slug_no_type_prefix(self):
        """_slugify should not produce type-prefixed IDs."""
        slug = _slugify("Smith-Important Work-2023")
        assert not slug.startswith("book-")
        assert not slug.startswith("journal_article-")
        assert not slug.startswith("book_chapter-")

    def test_slug_contains_author_title_year(self):
        """Slug should contain surname-title-year components."""
        slug = _slugify("Smith-Important Work-2023")
        assert "smith" in slug
        assert "2023" in slug
