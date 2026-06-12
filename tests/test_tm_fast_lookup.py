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


class TestIndexStructures:
    """Direct assertions on the index internals — query methods are not
    wired to the index yet (later tasks), so these are the only tests
    that actually exercise _sync_index/_candidates_for at this stage."""

    def test_sync_index_builds_parallel_structures(self, tm):
        tm._sync_index()
        n = len(tm.entries)
        assert tm._indexed_len == n
        assert len(tm._src_low) == n
        assert len(tm._tgt_low) == n
        # lowercase mirrors match entries positionally
        for i, e in enumerate(tm.entries):
            assert tm._src_low[i] == (e.get("source") or "").lower()
            assert tm._tgt_low[i] == (e.get("target") or "").lower()
        # vocabulary is sorted and consistent with the posting dict
        assert tm._inv_tokens == sorted(tm._inv)
        assert "dancers" in tm._inv
        # fuzzy choice lists stay aligned
        assert len(tm._fuzzy_sources) == len(tm._fuzzy_ids)
        for src, i in zip(tm._fuzzy_sources, tm._fuzzy_ids):
            assert tm.entries[i]["source"] == src

    def test_sync_index_is_incremental(self, tm):
        tm._sync_index()
        before = tm._indexed_len
        tm.entries.append({
            "source": "Xylophone quartet premiere",
            "target": "Premiera kvarteta ksilofonov",
            "origin": "working.tmx",
            "source_lang": "en", "target_lang": "sl",
            "raw_index": len(tm.entries), "t_index": len(tm.entries),
        })
        tm._sync_index()
        assert tm._indexed_len == before + 1
        assert "xylophone" in tm._inv
        assert tm._inv["xylophone"] == [before]

    def test_candidates_for_prefix_expansion(self, tm):
        tm._sync_index()
        # 'dance' must reach the entry containing 'dancers' via token prefix
        cand = tm._candidates_for(["dance"])
        assert any("dancers" in tm._src_low[i] for i in cand)
        # unknown word yields no candidates
        assert tm._candidates_for(["qqqxyzzy"]) == set()

    def test_reindex_entry_refreshes_mirrors(self, tm):
        tm._sync_index()
        i = next(
            idx for idx, e in enumerate(tm.entries)
            if e["source"].startswith("The dancers")
        )
        tm.entries[i]["target"] = "Plesalke so se premikale po odru"
        tm._reindex_entry(i)
        assert tm._tgt_low[i] == "plesalke so se premikale po odru"
        assert i in tm._inv["plesalke"]


class TestConcordance:
    def test_prefix_match(self, tm):
        # 'dance' must hit the entry containing 'dancers' (token prefix).
        hits = tm.search_concordance("dance", top_n=5)
        assert any("dancers" in h["source"] for h in hits)

    def test_result_shape_unchanged(self, tm):
        hits = tm.search_concordance("choreography dramaturgy", top_n=5)
        assert hits
        h = hits[0]
        for key in ("source", "target", "relevance", "_seg_len",
                    "kwic_source", "kwic_target"):
            assert key in h, f"missing key {key!r} — UI contract broken"
        assert h["relevance"] == 1.0  # both words matched

    def test_full_coverage_ranks_first(self, tm):
        tm.upsert_runtime_pair(
            "Only choreography here", "Samo koreografija tukaj", "en", "sl"
        )
        hits = tm.search_concordance("choreography dramaturgy", top_n=5)
        rels = [h["relevance"] for h in hits]
        assert rels == sorted(rels, reverse=True)
        assert "dramaturgy" in hits[0]["source"]

    def test_no_index_tokens_returns_empty(self, tm):
        assert tm.search_concordance("qqqqxyzzy", top_n=5) == []

    def test_long_query_capped_by_rarity(self, tm):
        # >12 unique words forces the max_words rarity trim; the rare
        # word 'choreography' must survive the cap and drive hits.
        noise = (
            "the of and in on at to for with from by as is was were be "
            "been about into over under"
        )
        assert len(set(noise.split())) > 12  # guard: branch actually taken
        hits = tm.search_concordance(noise + " choreography", top_n=5)
        assert any("choreography" in h["source"] for h in hits)
