# tests/test_cobiss_classifier.py
#
# Unit tests for cobiss_classifier.py — Phase 3.
# Tests entry classification, institution classification, and curator detection.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.cobiss_parser import CobissEntry, CobissAgent
from translate_core.cobiss_classifier import (
    classify_entry,
    classify_institution_kind,
    is_curator,
    CURATOR_SLUG,
    CONTAINER_TYPES,
    CITED_TYPES,
    INSTITUTION_KINDS,
    AGENT_ROLES,
)


# ======================================================================
# Tests: is_curator detection
# ======================================================================


class TestIsCurator:
    def test_curator_exact(self):
        agent = CobissAgent(last_name="BELINA", first_name="Urban")
        assert is_curator(agent) is True

    def test_curator_diacritic(self):
        agent = CobissAgent(last_name="Belina", first_name="Urban")
        assert is_curator(agent) is True

    def test_not_curator(self):
        agent = CobissAgent(last_name="OKRI", first_name="Ben")
        assert is_curator(agent) is False

    def test_curator_with_initial(self):
        """Initial-only first name 'U.' doesn't contain 'urban' — not a match."""
        agent = CobissAgent(last_name="BELINA", first_name="U.", roles=[])
        assert is_curator(agent) is False
# ======================================================================
# Tests: classify_entry — the curator's own works
# ======================================================================


class TestClassifyOwnWorks:
    """When the curator is first author with no translator role → cited type."""
    def test_book_author(self):
        entry = CobissEntry(
            entry_number=1,
            agents=[CobissAgent(last_name="BELINA", first_name="Urban", roles=["author"])],
            title="Some Book",
            isbn=["978-1-234567-89-0"],
            publisher="Maska",
            year=2020,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype in CITED_TYPES
        assert role == "author"

    def test_journal_article_author(self):
        entry = CobissEntry(
            entry_number=2,
            agents=[CobissAgent(last_name="BELINA", first_name="Urban", roles=["author"])],
            title="Some Article",
            issn="1234-5678",
            journal_name="Journal of Stuff",
            year=2019,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype in ("journal_article", "magazine_article")
        assert role == "author"


# ======================================================================
# Tests: classify_entry — translations (container types)
# ======================================================================


class TestClassifyTranslations:
    """When the curator is translator or not listed → container type."""
    def test_book_translation(self):
        entry = CobissEntry(
            entry_number=3,
            agents=[
                CobissAgent(last_name="SMITH", first_name="John", roles=["author"]),
                CobissAgent(last_name="BELINA", first_name="Urban", roles=["translator"]),
            ],
            title="Some Translated Book",
            isbn=["978-0-123456-78-9"],
            publisher="Press",
            year=2018,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "book_translation"
        assert role == "translator"

    def test_article_translation(self):
        entry = CobissEntry(
            entry_number=4,
            agents=[
                CobissAgent(last_name="DOE", first_name="Jane", roles=["author"]),
                CobissAgent(last_name="BELINA", first_name="Urban", roles=["translator"]),
            ],
            title="Some Article",
            issn="1234-5678",
            journal_name="Journal of Trans",
            year=2017,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "article_translation"
        assert role == "translator"

    def test_editor_produces_container(self):
        entry = CobissEntry(
            entry_number=5,
            agents=[
                CobissAgent(last_name="DOE", first_name="Jane", roles=["author"]),
                CobissAgent(last_name="BELINA", first_name="Urban", roles=["editor"]),
            ],
            title="Some Edited Book",
            isbn=["978-0-111111-22-3"],
            publisher="Press",
            year=2016,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype in CONTAINER_TYPES
        assert role == "editor"

    def test_no_curator_agent_implies_container(self):
        """When the curator is not among agents → implicit translator → container."""
        entry = CobissEntry(
            entry_number=6,
            agents=[CobissAgent(last_name="DOE", first_name="Jane", roles=["author"])],
            title="Some Foreign Book",
            isbn=["978-0-999999-88-7"],
            publisher="Press",
            year=2015,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype in CONTAINER_TYPES
        assert role == "translator"

    def test_festival_programme_with_curator_role(self):
        entry = CobissEntry(
            entry_number=7,
            agents=[CobissAgent(last_name="BELINA", first_name="Urban", roles=["curator"])],
            title="Festival Programme 2024",
            year=2024,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "festival_programme"
        assert role == "curator"

    def test_exhibition_catalogue(self):
        entry = CobissEntry(
            entry_number=8,
            agents=[CobissAgent(last_name="BELINA", first_name="Urban", roles=["editor"])],
            title="Katalog razstave: Moderna umetnost",
            year=2023,
            raw_text="",
        )
        ptype, role = classify_entry(entry)
        assert ptype == "exhibition_catalogue"


# ======================================================================
# Tests: classify_institution_kind
# ======================================================================


class TestClassifyInstitutionKind:
    def test_publisher(self):
        assert classify_institution_kind("Maska") == "publisher"

    def test_gallery(self):
        assert classify_institution_kind("Galerija Moderna") == "gallery"

    def test_publisher(self):
        assert classify_institution_kind("Založba Maska") == "publisher"

    def test_university(self):
        assert classify_institution_kind("Univerza v Ljubljani") == "university"

    def test_festival(self):
        assert classify_institution_kind("Festival Ljubljana") == "festival"

    def test_theatre(self):
        assert classify_institution_kind("Gledališče Mladinsko") == "theatre"

    def test_journal(self):
        assert classify_institution_kind("Revija Maska") == "journal"

    def test_sponsor(self):
        assert classify_institution_kind("Sponsor d.o.o.") == "sponsor"

    def test_country(self):
        assert classify_institution_kind("Republika Slovenija") == "country"

    def test_other(self):
        assert classify_institution_kind("Random Organization") == "other"


# ======================================================================
# Tests: O-constraint validation
# ======================================================================


class TestOConstraints:
    def test_o13_valid_roles(self):
        for role in AGENT_ROLES:
            assert role in {"author", "translator", "editor", "curator", "artist",
                           "interviewer", "interviewee", "choreographer", "director",
                           "performer", "dancer", "composer", "dramaturg", "agent"}

    def test_o14_valid_kinds(self):
        for kind in INSTITUTION_KINDS:
            assert kind in {"publisher", "gallery", "museum", "university",
                           "festival", "theatre", "journal", "organization",
                           "sponsor", "country", "other"}

    def test_o16_container_types_valid(self):
        for ct in CONTAINER_TYPES:
            assert ct in {"book_translation", "article_translation",
                         "festival_programme", "exhibition_catalogue"}

    def test_curator_slug_matches_ontology(self):
        """The curator's agent slug matches O-2 NFKD normalization."""
        assert CURATOR_SLUG == "urban-belina"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])