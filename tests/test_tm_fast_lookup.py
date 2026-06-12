"""Index-backed TM lookup: incremental sync, prefix concordance, fuzzy recall."""
from __future__ import annotations

import pytest

from translate_core.tm import TranslationMemory


@pytest.fixture
def tm(tmp_path) -> TranslationMemory:
    """Empty TM dir; populate via upsert_runtime_pair (exercises incremental
    indexing — entries arrive AFTER _load_all, like runtime confirms)."""
    t = TranslationMemory(tm_dir=tmp_path)
    t.upsert_runtime_pair(
        "The dancers moved across the stage in silence",
        "Plesalci so se v tišini premikali po odru",
        "en", "sl",
    )
    t.upsert_runtime_pair(
        "A short note on choreography and dramaturgy",
        "Kratka opomba o koreografiji in dramaturgiji",
        "en", "sl",
    )
    t.upsert_runtime_pair("Uvod", "Introduction", "sl", "en")
    return t


class TestIndexSync:
    def test_upserted_entries_visible_without_reload(self, tm):
        hits = tm.search_concordance("choreography", top_n=5)
        assert hits, "entry added via upsert_runtime_pair must be findable"
        assert "koreografiji" in hits[0]["target"]

    def test_direct_append_visible(self, tm):
        # main.py historically appended straight to tm.entries; lazy
        # _sync_index must pick the tail up at the next query.
        tm.entries.append({
            "source": "An entirely novel zugzwang situation",
            "target": "Povsem nova zugzwang situacija",
            "origin": "working.tmx",
            "source_lang": "en", "target_lang": "sl",
            "raw_index": len(tm.entries), "t_index": len(tm.entries),
        })
        hits = tm.search_concordance("zugzwang", top_n=5)
        assert len(hits) == 1

    def test_in_place_target_update_reindexed(self, tm):
        tm.upsert_runtime_pair(
            "The dancers moved across the stage in silence",
            "Plesalke so se v tišini premikale po odru",  # changed target
            "en", "sl",
        )
        hits = tm.search_concordance("plesalke", top_n=5)
        assert hits and "Plesalke" in hits[0]["target"]
        fuzzy = tm.lookup_fuzzy(
            "The dancers moved across the stage in silence", threshold=95.0
        )
        assert fuzzy and "Plesalke" in fuzzy[0]["target"]
