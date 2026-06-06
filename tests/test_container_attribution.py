"""Phase 6 — failing tests for translate_core.container_attribution.

Covers the 8 cases (a–h) defined in docs/phase6_blueprint.md §"Test plan".

The module under test does not yet exist; every test must fail with
ModuleNotFoundError or AttributeError against current code.

Tests build synthetic tm_entries lists locally — no TMX or DB access.
Imports are performed inside each test so collection stays green and
every case fails individually with the correct reason.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the project root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _mod():
    """Import the (yet-to-exist) module fresh inside each test."""
    from translate_core import container_attribution as m  # noqa: F401

    # Touch each required attribute so missing-attribute cases also fail loudly.
    for name in (
        "Anchor",
        "AttributionResult",
        "attribute_segments_to_containers",
        "load_curator_anchors",
        "load_ngram_anchors",
    ):
        getattr(m, name)
    return m


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _entry(
    origin: str,
    t_index: int,
    raw_index: int | None = None,
    source_lang: str = "en",
    target_lang: str = "sl",
) -> dict:
    """Build a synthetic TM entry dict matching the contract in the blueprint."""
    return {
        "origin": origin,
        "t_index": t_index,
        "raw_index": raw_index if raw_index is not None else t_index,
        "source_lang": source_lang,
        "target_lang": target_lang,
    }


# ----------------------------------------------------------------------
# (a) Basic chronological propagation
# ----------------------------------------------------------------------


class TestChronologicalPropagation:
    """Blueprint test (a)."""

    def test_anchor_propagates_to_following_entries_until_next_anchor(self):
        m = _mod()
        origin = "book.tmx"
        entries = [
            _entry(origin, t_index=5),
            _entry(origin, t_index=15),
            _entry(origin, t_index=40),
            _entry(origin, t_index=55),
        ]
        anchors = [
            m.Anchor(origin=origin, t_index=10, container_id="book-a", source="curator"),
            m.Anchor(origin=origin, t_index=50, container_id="book-b", source="curator"),
        ]

        result = m.attribute_segments_to_containers(anchors, entries)

        assert isinstance(result, m.AttributionResult)
        # t=5 comes before the first anchor → unanchored
        assert (origin, 5) in result.unanchored
        # t=15 and t=40 inherit book-a; t=55 flips to book-b
        assert result.attributed[(origin, 15)] == "book-a"
        assert result.attributed[(origin, 40)] == "book-a"
        assert result.attributed[(origin, 55)] == "book-b"
        # t=5 must NOT be attributed
        assert (origin, 5) not in result.attributed


# ----------------------------------------------------------------------
# (b) Conflict detection
# ----------------------------------------------------------------------


class TestConflictDetection:
    """Blueprint test (b)."""

    def test_two_anchors_same_t_index_different_container(self):
        m = _mod()
        origin = "book.tmx"
        entries = [_entry(origin, t_index=100), _entry(origin, t_index=120)]
        anchors = [
            m.Anchor(origin=origin, t_index=100, container_id="book-x", source="curator"),
            m.Anchor(origin=origin, t_index=100, container_id="book-y", source="ngram"),
        ]

        result = m.attribute_segments_to_containers(anchors, entries)

        # No attribution at the conflicting t_index
        assert (origin, 100) not in result.attributed
        # Exactly one conflict dict reported
        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]
        assert conflict["origin"] == origin
        assert conflict["t_index"] == 100
        assert sorted(conflict["container_ids"]) == ["book-x", "book-y"]
        assert sorted(conflict["sources"]) == ["curator", "ngram"]


# ----------------------------------------------------------------------
# (c) Unanchored list
# ----------------------------------------------------------------------


class TestUnanchoredList:
    """Blueprint test (c)."""

    def test_entries_before_first_anchor_are_unanchored(self):
        m = _mod()
        origin = "book.tmx"
        entries = [
            _entry(origin, t_index=5),
            _entry(origin, t_index=15),
            _entry(origin, t_index=35),
        ]
        anchors = [
            m.Anchor(origin=origin, t_index=30, container_id="book-a", source="curator"),
        ]

        result = m.attribute_segments_to_containers(anchors, entries)

        assert (origin, 5) in result.unanchored
        assert (origin, 15) in result.unanchored
        assert result.attributed == {(origin, 35): "book-a"}


# ----------------------------------------------------------------------
# (d) t_index, not raw_index, drives ordering
# ----------------------------------------------------------------------


class TestTIndexDrivesOrdering:
    """Blueprint test (d)."""

    def test_t_index_used_for_attribution_not_raw_index(self):
        m = _mod()
        origin = "book.tmx"
        # First scenario: anchor at t_index=0 must attribute the entry at
        # (raw=100, t=0) regardless of its raw_index.
        entries = [
            _entry(origin, t_index=0, raw_index=100),
            _entry(origin, t_index=100, raw_index=0),
        ]
        anchors = [
            m.Anchor(origin=origin, t_index=0, container_id="book-a", source="curator"),
        ]

        result = m.attribute_segments_to_containers(anchors, entries)
        assert result.attributed.get((origin, 0)) == "book-a"

        # Second scenario: anchor at t_index=50, entry at t=0 (raw=100) must be
        # unanchored — order strictly follows t_index, not raw_index.
        entries2 = [
            _entry(origin, t_index=0, raw_index=100),
            _entry(origin, t_index=100, raw_index=0),
        ]
        anchors2 = [
            m.Anchor(origin=origin, t_index=50, container_id="book-b", source="curator"),
        ]
        result2 = m.attribute_segments_to_containers(anchors2, entries2)
        assert (origin, 0) in result2.unanchored
        assert result2.attributed.get((origin, 100)) == "book-b"


# ----------------------------------------------------------------------
# (e) Multi-pair corpus
# ----------------------------------------------------------------------


class TestMultiPairCorpus:
    """Blueprint test (e)."""

    def test_per_origin_attribution_isolates_pairs(self):
        m = _mod()
        en_origin = "en-sl.tmx"
        hr_origin = "hr-sl.tmx"
        entries = [
            _entry(en_origin, t_index=10, source_lang="en", target_lang="sl"),
            _entry(en_origin, t_index=20, source_lang="en", target_lang="sl"),
            _entry(hr_origin, t_index=10, source_lang="hr", target_lang="sl"),
            _entry(hr_origin, t_index=20, source_lang="hr", target_lang="sl"),
        ]
        anchors = [
            m.Anchor(origin=en_origin, t_index=10, container_id="book-en", source="curator"),
            m.Anchor(origin=hr_origin, t_index=10, container_id="book-hr", source="curator"),
        ]

        result = m.attribute_segments_to_containers(anchors, entries)

        assert result.attributed[(en_origin, 10)] == "book-en"
        assert result.attributed[(en_origin, 20)] == "book-en"
        assert result.attributed[(hr_origin, 10)] == "book-hr"
        assert result.attributed[(hr_origin, 20)] == "book-hr"

        # No cross-origin leakage.
        for (origin, _t), cid in result.attributed.items():
            if origin == en_origin:
                assert cid == "book-en"
            elif origin == hr_origin:
                assert cid == "book-hr"

        # origins_by_pair preserves actual language codes per origin
        assert result.origins_by_pair[en_origin] == ("en", "sl")
        assert result.origins_by_pair[hr_origin] == ("hr", "sl")


# ----------------------------------------------------------------------
# (f) origins_by_pair surface
# ----------------------------------------------------------------------


class TestOriginsByPairSurface:
    """Blueprint test (f)."""

    def test_origins_by_pair_keys_and_values(self):
        m = _mod()
        a = "a.tmx"
        b = "b.tmx"
        entries = [
            _entry(a, t_index=0, source_lang="en", target_lang="sl"),
            _entry(a, t_index=1, source_lang="en", target_lang="sl"),
            _entry(b, t_index=0, source_lang="de", target_lang="sl"),
        ]
        anchors: list = []

        result = m.attribute_segments_to_containers(anchors, entries)

        assert set(result.origins_by_pair.keys()) == {a, b}
        assert result.origins_by_pair[a] == ("en", "sl")
        assert result.origins_by_pair[b] == ("de", "sl")


# ----------------------------------------------------------------------
# (g) Missing ngram file returns []
# ----------------------------------------------------------------------


class TestMissingNgramFile:
    """Blueprint test (g)."""

    def test_load_ngram_anchors_missing_path_returns_empty(self, tmp_path):
        m = _mod()
        bogus = tmp_path / "definitely_does_not_exist.json"
        assert not bogus.exists()
        result = m.load_ngram_anchors(path=str(bogus), tm_entries=[])
        assert result == []

    def test_load_curator_anchors_missing_path_returns_empty(self, tmp_path):
        m = _mod()
        bogus = tmp_path / "missing_curator.json"
        assert not bogus.exists()
        result = m.load_curator_anchors(path=str(bogus), tm_entries=[])
        assert result == []


# ----------------------------------------------------------------------
# (h) Agree-deduplication
# ----------------------------------------------------------------------


class TestAgreeDeduplication:
    """Blueprint test (h)."""

    def test_two_anchors_same_container_dedup_silently(self):
        m = _mod()
        origin = "book.tmx"
        entries = [_entry(origin, t_index=42), _entry(origin, t_index=43)]
        anchors = [
            m.Anchor(origin=origin, t_index=42, container_id="book-a", source="curator"),
            m.Anchor(origin=origin, t_index=42, container_id="book-a", source="ngram"),
        ]

        result = m.attribute_segments_to_containers(anchors, entries)

        # No conflict reported when both anchors agree.
        assert result.conflicts == []
        # Exactly one attribution at t=42 (and propagated to t=43).
        assert result.attributed[(origin, 42)] == "book-a"
        assert result.attributed[(origin, 43)] == "book-a"


# ----------------------------------------------------------------------
# Loader coverage — curator JSON → t_index anchors via per-origin natural order
# ----------------------------------------------------------------------


class TestLoadCuratorAnchorsFromFixture:
    """Loader sanity — seg_idx → t_index translation via per-origin natural order."""

    def test_seg_idx_resolves_to_t_index(self, tmp_path):
        m = _mod()
        origin = "book.tmx"
        # Per-origin natural order (what tm.entries yields after the
        # raw_index sort done by _build_compat_entries).
        entries = [
            _entry(origin, t_index=11, raw_index=0),  # seg_idx=0
            _entry(origin, t_index=22, raw_index=1),  # seg_idx=1
            _entry(origin, t_index=33, raw_index=2),  # seg_idx=2
        ]
        fixture = tmp_path / "segment_title_attribution.json"
        fixture.write_text(
            json.dumps({origin: {"1": ["source:book-a"]}}),
            encoding="utf-8",
        )

        anchors = m.load_curator_anchors(path=str(fixture), tm_entries=entries)

        assert len(anchors) == 1
        a = anchors[0]
        assert a.origin == origin
        # seg_idx=1 → entries[1].t_index = 22
        assert a.t_index == 22
        # container_id is stored WITHOUT the "source:" prefix
        assert a.container_id == "book-a"
        assert a.source == "curator"

    def test_out_of_range_seg_idx_is_skipped(self, tmp_path):
        m = _mod()
        origin = "book.tmx"
        entries = [_entry(origin, t_index=11, raw_index=0)]
        fixture = tmp_path / "segment_title_attribution.json"
        fixture.write_text(
            json.dumps({origin: {"5": ["source:book-a"]}}),
            encoding="utf-8",
        )

        anchors = m.load_curator_anchors(path=str(fixture), tm_entries=entries)

        assert anchors == []


class TestLoadNgramAnchorsFromFixture:
    """Loader sanity — ngram JSON uses a single string value per seg_idx."""

    def test_string_value_parsed(self, tmp_path):
        m = _mod()
        origin = "book.tmx"
        entries = [
            _entry(origin, t_index=100, raw_index=0),
            _entry(origin, t_index=200, raw_index=1),
        ]
        fixture = tmp_path / "segment_attribution_ngram.json"
        fixture.write_text(
            json.dumps({origin: {"0": "source:book-ngram"}}),
            encoding="utf-8",
        )

        anchors = m.load_ngram_anchors(path=str(fixture), tm_entries=entries)

        assert len(anchors) == 1
        a = anchors[0]
        assert a.origin == origin
        assert a.t_index == 100
        assert a.container_id == "book-ngram"
        assert a.source == "ngram"
