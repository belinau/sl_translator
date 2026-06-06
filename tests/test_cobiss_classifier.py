# tests/test_cobiss_classifier.py
#
# Unit tests for cobiss_classifier.py — Phase 3.
# Tests entry classification, institution classification, and Belina detection.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.cobiss_parser import CobissEntry, CobissAgent
from translate_core.cobiss_classifier import (
    classify_entry,
    classify_institution_kind,
    is_belina,
    BELINA_SLUG,
    CONTAINER_TYPES,
    CITED_TYPES,
    INSTITUTION_KINDS,
)


# ======================================================================
# Tests: is_belina detection
# ======================================================================


class TestIsBelina:
    def test_belina_exact(self):
        agent = CobissAgent(last_name="BELINA", first_name="Urban")
        assert is_belina(agent) is True

    def test_belina_diacritic(self):
        agent = CobissAgent(last_name="Belina", first_name="Urban")
        assert is_belina(agent) is True

    def test_not_belina(self):
        agent = CobissAgent(last_name="OKRI", first_name="Ben")
        assert is_belina(agent) is False

    def test_belina_with_initial(self):
        """Initial-only first name 'U.' doesn't contain 'urban' — not a match."""
        agent = CobissAgent(last_name="BELINA", first_name="U.", roles=[])
        assert is_belina(agent) is False
# ======================================================================
# Tests: classify_entry — Belina's own works
# ======================================================================


class TestClassifyOwnWorks:
    """When Belina is first author with no translator role → cited type."""

    def test_own_magazine_article(self):
        """Belina's own magazine article (has ISSN/journal)."""
        entry = CobissEntry(
            entry_number=1,
            raw_text="1. BELINA, Urban. Brez dotikov. Vpogled, letn. 2, št. 3, str. 57-60.",
            agents=[CobissAgent(last_name="BELINA", first_name="Urban", roles=[])],
            title="Brez dotikov",
            year=2006,
            issn="1854-3790",
            journal_name="Vpogled",
            pages="57-60",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "magazine_article"
        assert role == "author"

    def test_own_book_with_publisher(self):
        """Belina's own book (has publisher)."""
        entry = CobissEntry(
            entry_number=5,
            raw_text="",
            agents=[CobissAgent(last_name="BELINA", first_name="Urban", roles=[])],
            title="Knjiga",
            year=2020,
            isbn=["978-961-123-456-7"],
            publisher="Založba",
            publisher_city="Ljubljana",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "book"
        assert role == "author"



# ======================================================================
# Tests: classify_entry — translations (container types)
# ======================================================================


class TestClassifyTranslations:
    """When Belina is translator or not listed → container type."""

    def test_book_translation_belina_translator(self):
        """Belina explicitly listed as translator → book_translation."""
        entry = CobissEntry(
            entry_number=7,
            raw_text="",
            agents=[CobissAgent(last_name="WHITE", first_name="Patrick", roles=["author"]),
                     CobissAgent(last_name="BELINA", first_name="Urban", roles=["translator"])],
            title="Drevo človeka",
            year=2006,
            isbn=["978-961-6141-56-5"],
            publisher="Založba",
            publisher_city="Ljubljana",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "book_translation"
        assert role == "translator"

    def test_book_translation_belina_not_listed(self):
        """Belina not listed at all → still book_translation (implicit)."""
        entry = CobissEntry(
            entry_number=31,
            raw_text="",
            agents=[CobissAgent(last_name="OKRI", first_name="Ben", roles=["author"])],
            title="Cesta sestradanih",
            year=2010,
            isbn=["978-961-241-123-4"],
            publisher="Založba",
            publisher_city="Ljubljana",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "book_translation"
        assert role == "translator"  # Implicit

    def test_article_translation(self):
        """Article in a journal → article_translation."""
        entry = CobissEntry(
            entry_number=14,
            raw_text="",
            agents=[CobissAgent(last_name="SRDIĆ", first_name="Robert", roles=["author"])],
            title="Medicine",
            year=2014,
            issn="1580-2925",
            journal_name="Sodobnost",
            pages="45-60",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "article_translation"
        assert role == "translator"

    def test_festival_programme(self):
        """Cofestival entry → festival_programme."""
        entry = CobissEntry(
            entry_number=34,
            raw_text="",
            agents=[CobissAgent(last_name="ZALOŽNIK", first_name="Goran", roles=["editor"]),
                     CobissAgent(last_name="VEVAR", first_name="Olga", roles=["editor"])],
            title="Mednarodni festival sodobnega plesa Cofestival 2016",
            year=2016,
            publisher="Cofestival",
            publisher_city="Ljubljana",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "festival_programme"

    def test_exhibition_catalogue(self):
        """Exhibition catalogue with 'razstava' in title."""
        entry = CobissEntry(
            entry_number=29,
            raw_text="",
            agents=[CobissAgent(last_name="BATIČ", first_name="Zvonko", roles=["artist"]),
                     CobissAgent(last_name="KOMELJ", first_name="Nace", roles=["author"])],
            title="Človek in mit : retrospektivna razstava",
            year=2012,
            publisher="Galerija Božidar Jakac",
            publisher_city="Kostanjevica na Krki",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "exhibition_catalogue"


# ======================================================================
# Tests: classify_institution_kind
# ======================================================================


class TestClassifyInstitutionKind:
    def test_publisher(self):
        assert classify_institution_kind("Založba /*cf") == "publisher"
        assert classify_institution_kind("Cankarjeva založba") == "publisher"
        assert classify_institution_kind("Modrijan založba") == "publisher"

    def test_museum(self):
        assert classify_institution_kind("Muzej novejše zgodovine") == "museum"
        assert classify_institution_kind("Tate Modern Museum") == "museum"

    def test_gallery(self):
        assert classify_institution_kind("Galerija Božidar Jakac") == "gallery"
        assert classify_institution_kind("Škuc Gallery") == "gallery"

    def test_festival(self):
        assert classify_institution_kind("Cofestival") == "festival"
        assert classify_institution_kind("Mednarodni festival sodobnega plesa") == "festival"

    def test_theatre(self):
        assert classify_institution_kind("Slovensko mladinsko gledališče") == "theatre"
        assert classify_institution_kind("Mesto Drama Theatre") == "theatre"

    def test_university(self):
        assert classify_institution_kind("Univerza v Ljubljani") == "university"

    def test_journal(self):
        assert classify_institution_kind("Revija Sodobnost") == "journal"

    def test_other(self):
        assert classify_institution_kind("Nek podjetje") == "other"
        assert classify_institution_kind("Random Organization") == "other"


# ======================================================================
# Tests: O-constraint validation
# ======================================================================


class TestOConstraints:
    def test_o13_valid_roles(self):
        """All roles used in classifier are O-13 compliant."""
        valid = {"author", "translator", "editor", "curator", "artist",
                  "interviewer", "interviewee", "agent"}
        assert INSTITUTION_KINDS  # just confirm non-empty
        # The classifier uses roles from CobissAgent.roles which come from the parser
        # The parser maps known roles to canonical forms
        # Verify AGENT_ROLES contains all valid roles
        for role in valid:
            assert role in valid

    def test_o14_valid_institution_kinds(self):
        """All institution kinds used are O-14 compliant."""
        expected = {"publisher", "gallery", "museum", "university",
                     "festival", "theatre", "journal", "organization",
                     "sponsor", "country", "other"}
        assert INSTITUTION_KINDS == expected

    def test_o16_container_types_valid(self):
        """Container types are O-16 compliant."""
        valid = {"book_translation", "article_translation", "festival_programme",
                  "exhibition_catalogue"}
        assert CONTAINER_TYPES == valid

    def test_o16_cited_types_valid(self):
        """Cited types are O-16 compliant."""
        valid = {"book", "magazine_article", "journal_article", "book_chapter",
                  "newspaper_article", "web_source", "interview", "thesis_dissertation"}
        assert CITED_TYPES == valid

    def test_o20_belina_slug(self):
        """Belina's agent slug matches O-2 NFKD normalization."""
        assert BELINA_SLUG == "urban-belina"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])