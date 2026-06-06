"""TDD red-phase tests for Phase 1 TMX chronological awareness.

These tests assert the existence and behavior of:
  * ``translate_core.tm_timecodes.read_tmx_with_timecodes`` — a new module/function
  * ``TranslationMemory.iter_chronological`` — a new method on the loader
  * Three new keys (``raw_index``, ``creationdate``, ``t_index``) on every
    entry dict produced by the TMX loader

Per the design in ``docs/superpowers/plans/2026-06-06-parsing-simplification.md``
Phase 1, ``self.entries`` MUST remain in natural load order; chronological
access is exposed via ``t_index`` and ``iter_chronological``.

These tests are intentionally failing at red-phase: production code does
not yet exist. They should fail with ``ImportError`` / ``ModuleNotFoundError``
/ ``AttributeError`` referencing the missing symbols.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "tmx_unordered.tmx"


# ---------------------------------------------------------------------------
# (a) read_tmx_with_timecodes returns entries in natural file order
# ---------------------------------------------------------------------------


def test_read_tmx_with_timecodes_returns_entries_in_natural_file_order():
    from translate_core.tm_timecodes import read_tmx_with_timecodes

    entries = read_tmx_with_timecodes(FIXTURE)

    assert isinstance(entries, list)
    assert len(entries) == 4

    # Natural file order is preserved in the returned list.
    assert [e["raw_index"] for e in entries] == [0, 1, 2, 3]

    # Every entry carries the full key set.
    expected_keys = {
        "source",
        "target",
        "origin",
        "raw_index",
        "creationdate",
        "t_index",
    }
    for entry in entries:
        assert expected_keys.issubset(entry.keys()), (
            f"entry missing keys: {expected_keys - set(entry.keys())}"
        )


# ---------------------------------------------------------------------------
# (b) t_index reflects chronological order while list stays natural
# ---------------------------------------------------------------------------


def test_t_index_reflects_chronological_order_of_dated_entries():
    from translate_core.tm_timecodes import read_tmx_with_timecodes

    entries = read_tmx_with_timecodes(FIXTURE)

    # Fixture timecodes by natural index:
    #   [0] = 2024-01-01  -> chronologically last dated -> t_index == 2
    #   [1] = 2022-01-01  -> chronologically first      -> t_index == 0
    #   [2] = 2023-01-01  -> chronologically middle     -> t_index == 1
    assert entries[0]["t_index"] == 2
    assert entries[1]["t_index"] == 0
    assert entries[2]["t_index"] == 1


# ---------------------------------------------------------------------------
# (c) Dateless entries: creationdate=None, t_index after all dated entries
# ---------------------------------------------------------------------------


def test_dateless_entry_has_none_creationdate_and_trailing_t_index():
    from translate_core.tm_timecodes import read_tmx_with_timecodes

    entries = read_tmx_with_timecodes(FIXTURE)

    # Natural index 3 has no creationdate attribute.
    assert entries[3]["creationdate"] is None
    # It must be placed AFTER all dated entries in the chronological view.
    # With three dated entries (t_index 0, 1, 2), the dateless one gets 3.
    assert entries[3]["t_index"] == 3

    # And the three dated entries each carry a non-None creationdate string.
    for i in (0, 1, 2):
        assert isinstance(entries[i]["creationdate"], str)
        assert entries[i]["creationdate"]  # non-empty


# ---------------------------------------------------------------------------
# (d) TranslationMemory.iter_chronological() yields by ascending t_index
# ---------------------------------------------------------------------------


def test_translation_memory_iter_chronological_yields_in_t_index_order(tmp_path):
    # Isolate the TM directory so we only load our fixture.
    tm_dir = tmp_path / "tm"
    tm_dir.mkdir()
    shutil.copy(FIXTURE, tm_dir / "tmx_unordered.tmx")

    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir)

    chrono = list(tm.iter_chronological())
    assert [e["t_index"] for e in chrono] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# (e) iter_chronological(origin=...) filters by origin
# ---------------------------------------------------------------------------


def test_iter_chronological_filters_by_origin(tmp_path):
    tm_dir = tmp_path / "tm"
    tm_dir.mkdir()
    shutil.copy(FIXTURE, tm_dir / "tmx_unordered.tmx")

    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir)

    # Single-file fixture: filtering by its origin must yield the same
    # chronological sequence as the unfiltered call.
    filtered = list(tm.iter_chronological(origin="tmx_unordered.tmx"))
    assert [e["t_index"] for e in filtered] == [0, 1, 2, 3]
    for entry in filtered:
        assert entry["origin"] == "tmx_unordered.tmx"

    # A bogus origin yields nothing.
    assert list(tm.iter_chronological(origin="does_not_exist.tmx")) == []


# ---------------------------------------------------------------------------
# (f) self.entries remains in natural load order — editor invariant
# ---------------------------------------------------------------------------


def test_translation_memory_entries_remain_in_natural_load_order(tmp_path):
    tm_dir = tmp_path / "tm"
    tm_dir.mkdir()
    shutil.copy(FIXTURE, tm_dir / "tmx_unordered.tmx")

    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir)

    # raw_index of self.entries must be a contiguous range starting at 0.
    # This is the editor-side invariant: main.py:251 appends to self.entries
    # by content, and inline_test fixtures read self.entries[0] positionally.
    assert [e["raw_index"] for e in tm.entries] == list(range(len(tm.entries)))
    assert len(tm.entries) == 4
