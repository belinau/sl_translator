# tests/test_vl_extractor.py
#
# Unit tests for vl_extractor.py — Phase 0 constraints.
# These tests verify that the generic _call_batch path (kitchen-sink prompt)
# no longer emits work/artwork entities and cannot produce cited_work records.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.vl_extractor import (
    _BUILDERS,
    _USER_TEMPLATE,
    _make_person_record,
    _make_institution_record,
)

# Fixtures: segment text must contain the name for verification to pass
SRC_PERSON = "Firstname Lastname wrote about architecture"
SRC_INST = "Published by Some Publisher in Ljubljana"


# ======================================================================
# Tests: _BUILDERS dict no longer contains work or artwork
# ======================================================================


class TestBuildersNoWorkArtwork:
    """Phase 0 invariant: the generic batch prompt only emits person and institution."""

    def test_builders_has_person(self):
        assert "person" in _BUILDERS
        assert _BUILDERS["person"] is _make_person_record

    def test_builders_has_institution(self):
        assert "institution" in _BUILDERS
        assert _BUILDERS["institution"] is _make_institution_record

    def test_builders_no_work(self):
        """O-4 / O-16: 'work' builder removed — no cited_work from generic path."""
        assert "work" not in _BUILDERS

    def test_builders_no_artwork(self):
        """O-4 / O-16: 'artwork' builder removed — no artwork from generic path."""
        assert "artwork" not in _BUILDERS

    def test_builders_exactly_two_entries(self):
        """Generic batch prompt should only produce person + institution."""
        assert set(_BUILDERS.keys()) == {"person", "institution"}


# ======================================================================
# Tests: prompt template no longer mentions work or artwork
# ======================================================================


class TestPromptNoWorkArtwork:
    """O-6: VL prompt examples must be abstract; O-4: no work/artwork kinds."""

    def test_prompt_no_work_kind(self):
        assert '"work"' not in _USER_TEMPLATE
        assert "'work'" not in _USER_TEMPLATE
        # The word "work" should not appear as a kind label in the schema line
        kind_line = [l for l in _USER_TEMPLATE.splitlines() if "kind" in l]
        for line in kind_line:
            assert "work" not in line.lower() or "network" in line.lower()

    def test_prompt_no_artwork_kind(self):
        assert '"artwork"' not in _USER_TEMPLATE
        assert "'artwork'" not in _USER_TEMPLATE

    def test_prompt_only_person_institution(self):
        """The prompt must only list person and institution kinds."""
        assert "person" in _USER_TEMPLATE
        assert "institution" in _USER_TEMPLATE

    def test_prompt_no_real_names(self):
        """O-6: No real names in prompt examples — only abstract placeholders."""
        for line in _USER_TEMPLATE.splitlines():
            if '"kind"' in line or '"name"' in line or '"role"' in line:
                assert "Belina" not in line
                assert "Okri" not in line


# ======================================================================
# Tests: _make_person_record produces no cited_work
# ======================================================================


class TestPersonRecordNoCitedWork:
    """_make_person_record should never produce a record with project_type cited_work."""

    def test_person_record_kind_is_agent_person(self):
        rec = _make_person_record(
            {"name": "Firstname Lastname", "role": "author"},
            origin="test.tmx",
            seg_idx=0,
            src=SRC_PERSON,
            tgt="Prevod",
        )
        assert rec is not None
        assert rec["kind"] == "agent_person"
        assert rec.get("project_type") != "cited_work"

    def test_person_record_no_project_type_cited_work(self):
        rec = _make_person_record(
            {"name": "Firstname Lastname", "role": "translator"},
            origin="test.tmx",
            seg_idx=0,
            src=SRC_PERSON,
            tgt="Prevod",
        )
        assert rec is not None
        if "project_type" in rec:
            assert rec["project_type"] != "cited_work"


# ======================================================================
# Tests: _make_institution_record produces no cited_work
# ======================================================================


class TestInstitutionRecordNoCitedWork:
    def test_institution_record_kind(self):
        rec = _make_institution_record(
            {"name": "Some Publisher", "type": "publisher", "city": "Ljubljana"},
            origin="test.tmx",
            seg_idx=0,
            src=SRC_INST,
            tgt="Prevod",
        )
        assert rec is not None
        assert rec["kind"] == "institution"
        assert rec.get("project_type") != "cited_work"


# ======================================================================
# Tests: Simulated _call_batch output — work/artwork filtered
# ======================================================================


class TestSimulatedBatchOutput:
    """Simulate what _call_batch does with parsed entities: only person/institution
    should be processed. Work/artwork kinds are now filtered by _BUILDERS."""

    def test_work_kind_filtered(self):
        """If VL model hallucinates a 'work' kind, _BUILDERS.get('work') returns None."""
        assert _BUILDERS.get("work") is None

    def test_artwork_kind_filtered(self):
        """If VL model hallucinates an 'artwork' kind, _BUILDERS.get('artwork') returns None."""
        assert _BUILDERS.get("artwork") is None

    def test_person_kind_accepted(self):
        assert _BUILDERS.get("person") is _make_person_record

    def test_institution_kind_accepted(self):
        assert _BUILDERS.get("institution") is _make_institution_record

    def test_no_cited_work_from_batch_entities(self):
        """Processing a mix of entity kinds through _BUILDERS should produce
        no cited_work records — work and artwork kinds are simply skipped."""
        entities = [
            {"kind": "person", "name": "Firstname Lastname", "role": "author"},
            {"kind": "work", "title_en": "Some Title", "author": "Author"},
            {"kind": "artwork", "title_en": "Art Piece", "artist": "Artist"},
            {"kind": "institution", "name": "Some Publisher", "type": "publisher"},
        ]
        records = []
        for entity in entities:
            kind = entity.get("kind", "")
            builder = _BUILDERS.get(kind)
            if not builder:
                continue
            # Use src text containing the entity name so verification passes
            src = "Firstname Lastname published by Some Publisher"
            rec = builder(entity, "test.tmx", 0, src, "tgt text")
            if rec:
                records.append(rec)

        # Only person + institution records produced (work and artwork filtered)
        assert len(records) == 2
        assert all(r["kind"] != "cited_work" for r in records)
        assert records[0]["kind"] == "agent_person"
        assert records[1]["kind"] == "institution"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])