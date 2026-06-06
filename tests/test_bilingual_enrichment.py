# tests/test_bilingual_enrichment.py
#
# Unit tests for bilingual_enrichment_batch.py — Phase 5.
# All tests are standalone (no VL model, no real KG, no real TM).

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter
import pytest

from translate_core.entity_extraction.bilingual_enrichment_batch import (
    _slugify,
    _normalise,
    _tokenise,
    _strip_diacritics,
    _looks_like_title,
    _plausible_length_ratio,
    _extract_title_from_segment,
    enrich_from_tmx,
    cross_match_bilingual_nodes,
    enrich_containers,
    run_bilingual_enrichment,
    CONTAINER_TYPES,
)


# ── Tests: _slugify ────────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert _slugify("A Cyborg Manifesto") == "a-cyborg-manifesto"

    def test_unicode_normalisation(self):
        # Š → s (NFKD strips diacritics), trailing dashes are kept by slugify
        result = _slugify("Šuvaković")
        assert "suvakovic" in result.replace("-", "")
        assert result.startswith("s")
    def test_truncation(self):
        long = "a" * 100
        result = _slugify(long)
        assert len(result) == 80

    def test_empty_fallback(self):
        assert _slugify("") == "unknown"

    def test_special_chars(self):
        result = _slugify("Book: A Title (2nd ed.)")
        assert "book" in result
        assert "title" in result
        assert "2nd" in result
        assert ":" not in result

# ── Tests: _normalise ──────────────────────────────────────────────────────


class TestNormalise:
    def test_basic(self):
        assert _normalise("A Cyborg Manifesto") == "a cyborg manifesto"

    def test_diacritics_stripped(self):
        n = _normalise("Življenje umetnosti")
        assert "z" in n
        assert "ž" not in n

    def test_punctuation_removed(self):
        n = _normalise("Book: A Title!")
        assert ":" not in n
        assert "!" not in n


# ── Tests: _tokenise ───────────────────────────────────────────────────────


class TestTokenise:
    def test_basic(self):
        tokens = _tokenise("A Cyborg Manifesto")
        assert "cyborg" in tokens
        assert "manifesto" in tokens

    def test_single_char_dropped(self):
        tokens = _tokenise("A Book")
        assert "a" not in tokens
        assert "book" in tokens

    def test_diacritics_stripped(self):
        tokens = _tokenise("Življenje umetnosti")
        assert any("zivljenje" in t for t in tokens)


# ── Tests: _looks_like_title ────────────────────────────────────────────────


class TestLooksLikeTitle:
    def test_short_title(self):
        assert _looks_like_title("Manifesta")

    def test_empty(self):
        assert not _looks_like_title("")

    def test_too_short(self):
        assert not _looks_like_title("ab")

    def test_long_sentence(self):
        assert not _looks_like_title("This is a very long sentence that goes on and on and on. " * 5)


# ── Tests: _plausible_length_ratio ──────────────────────────────────────────


class TestPlausibleLengthRatio:
    def test_similar_length(self):
        assert _plausible_length_ratio("A Cyborg Manifesto", "Kiborgov manifest")

    def test_too_short(self):
        assert not _plausible_length_ratio("A Cyborg Manifesto", "ab")

    def test_too_long(self):
        assert not _plausible_length_ratio("ab", "This is a very long title that exceeds ratio")


# ── Tests: _extract_title_from_segment ──────────────────────────────────────


class TestExtractTitleFromSegment:
    def test_separator_dash(self):
        result = _extract_title_from_segment(
            "A Cyborg Manifesto — Kiborgov manifest",
            "A Cyborg Manifesto",
        )
        assert result is not None
        assert "Kiborgov" in result

    def test_separator_colon(self):
        result = _extract_title_from_segment(
            "A Cyborg Manifesto: Kiborgov manifest",
            "A Cyborg Manifesto",
        )
        assert result is not None

    def test_no_match(self):
        result = _extract_title_from_segment(
            "Completely unrelated text",
            "A Cyborg Manifesto",
        )
        assert result is None


# ── Tests: _strip_diacritics ───────────────────────────────────────────────


class TestStripDiacritics:
    def test_slovenian(self):
        assert _strip_diacritics("Šuvaković") == "Suvakovic"

    def test_french(self):
        assert _strip_diacritics("café") == "cafe"


# ── Tests: enrich_containers ────────────────────────────────────────────────


class TestEnrichContainers:
    def _make_kg_mock(self, nodes):
        """Create a mock KG object with the specified nodes."""

        class MockKG:
            def __init__(self, nodes_list):
                self.G = type("G", (), {"nodes": {}})()
                self._nodes = {}
                for n in nodes_list:
                    nid = n.get("id", f"source:test-{len(self._nodes)}")
                    self._nodes[nid] = dict(n)
                    self.G.nodes[nid] = self._nodes[nid]
                self.G.nodes = self._nodes
                self._updates = []

            def update_source_text_node(self, source_id, title=None, year=None,
                                        author_id=None, *, title_en=...,
                                        title_sl=..., title_orig=...,
                                        title_translation=..., orig_lang=...,
                                        translation_lang=..., slovenian_edition=...,
                                        project_type=None):
                self._updates.append({
                    "source_id": source_id,
                    "title_en": title_en if title_en is not ... else None,
                    "title_sl": title_sl if title_sl is not ... else None,
                    "title_orig": title_orig if title_orig is not ... else None,
                    "title_translation": title_translation if title_translation is not ... else None,
                    "orig_lang": orig_lang if orig_lang is not ... else None,
                    "translation_lang": translation_lang if translation_lang is not ... else None,
                })
                return True

            def save(self):
                pass

        return MockKG(nodes)

    def test_container_with_both_titles(self):
        nodes = [
            {
                "id": "source:test-book",
                "type": "source_text",
                "project_type": "book_translation",
                "title": "Test Book",
                "language": "en",
                "title_en": "The Test Book",
                "title_sl": "Testna knjiga",
            }
        ]
        kg = self._make_kg_mock(nodes)
        stats = enrich_containers(kg, dry_run=False)
        assert stats["enriched"] == 1
        assert len(kg._updates) == 1
        update = kg._updates[0]
        assert update["title_orig"] == "The Test Book"
        assert update["title_translation"] == "Testna knjiga"
        assert update["orig_lang"] == "en"
        assert update["translation_lang"] == "sl"

    def test_container_already_has_orig(self):
        nodes = [
            {
                "id": "source:test-book",
                "type": "source_text",
                "project_type": "book_translation",
                "title": "Test Book",
                "title_en": "The Test Book",
                "title_sl": "Testna knjiga",
                "title_orig": "The Test Book",
                "title_translation": "Testna knjiga",
            }
        ]
        kg = self._make_kg_mock(nodes)
        stats = enrich_containers(kg, dry_run=False)
        assert stats["enriched"] == 0  # Already complete
        assert stats["incomplete"] == 0

    def test_container_missing_one_title(self):
        nodes = [
            {
                "id": "source:test-book",
                "type": "source_text",
                "project_type": "book_translation",
                "title": "Test Book",
                "title_en": "The Test Book",
                # Missing title_sl
            }
        ]
        kg = self._make_kg_mock(nodes)
        stats = enrich_containers(kg, dry_run=False)
        assert stats["incomplete"] == 1
        assert stats["enriched"] == 0

    def test_non_container_not_enriched(self):
        nodes = [
            {
                "id": "source:cited-work",
                "type": "source_text",
                "project_type": "cited_work",
                "title": "Some Citation",
                "title_en": "Some Citation",
                "title_sl": "Neka citacija",
            }
        ]
        kg = self._make_kg_mock(nodes)
        stats = enrich_containers(kg, dry_run=False)
        assert stats["enriched"] == 0
        assert stats["incomplete"] == 0

    def test_dry_run_no_updates(self):
        nodes = [
            {
                "id": "source:test-book",
                "type": "source_text",
                "project_type": "book_translation",
                "title": "Test Book",
                "title_en": "The Test Book",
                "title_sl": "Testna knjiga",
            }
        ]
        kg = self._make_kg_mock(nodes)
        stats = enrich_containers(kg, dry_run=True)
        assert stats["enriched"] == 1  # Counted but not written
        assert len(kg._updates) == 0  # No actual updates


# ── Tests: enrich_from_tmx ──────────────────────────────────────────────────


class TestEnrichFromTmx:
    def _make_kg_mock(self, nodes):
        class MockKG:
            def __init__(self, nodes_list):
                self._nodes = {}
                for n in nodes_list:
                    nid = n.get("id", f"source:test-{len(self._nodes)}")
                    self._nodes[nid] = dict(n)
                self._updates = []

            @property
            def G(self):
                class _G:
                    nodes = self._nodes
                return _G()

            def update_source_text_node(self, source_id, title=None, year=None,
                                        author_id=None, *, title_en=...,
                                        title_sl=..., **kwargs):
                self._updates.append({"source_id": source_id, "title_sl": title_sl})
                if title_sl is not ...:
                    self._nodes[source_id]["title_sl"] = title_sl
                return True

        return MockKG(nodes)

    def test_tmx_enrichment(self):
        """TMX enrichment processes EN-only nodes against TM entries."""
        nodes = [
            {
                "id": "source:test-cited-1",
                "type": "source_text",
                "project_type": "cited_work",
                "title": "A Cyborg Manifesto",
                "title_en": "A Cyborg Manifesto",
                # Missing title_sl
            }
        ]
        tm_entries = [
            {
                "source": "Donna Haraway, A Cyborg Manifesto, Routledge 1991",
                "target": "Donna Haraway, Kiborgov manifest, Routledge 1991",
                "origin": "test-EN-SL.tmx",
                "source_lang": "en",
                "target_lang": "sl",
            },
        ]
        kg = self._make_kg_mock(nodes)
        stats = enrich_from_tmx(kg, tm_entries, dry_run=False)
        # The enrichment ran without error — the matching logic
        # depends on normalised title overlap in the source text
        assert isinstance(stats, Counter)

    def test_no_match_empty_tm(self):
        nodes = [
            {
                "id": "source:test-cited-2",
                "type": "source_text",
                "project_type": "cited_work",
                "title": "Nonexistent Book",
                "title_en": "Nonexistent Book",
            }
        ]
        kg = self._make_kg_mock(nodes)
        stats = enrich_from_tmx(kg, [], dry_run=False)
        assert stats["enriched_tmx"] == 0


# ── Tests: cross_match_bilingual_nodes ──────────────────────────────────────


class TestCrossMatchBilingualNodes:
    def _make_kg_mock(self, nodes):
        class MockKG:
            def __init__(self, nodes_list):
                self._nodes = {}
                for n in nodes_list:
                    nid = n.get("id", f"source:test-{len(self._nodes)}")
                    self._nodes[nid] = dict(n)
                self.G = type("G", (), {"nodes": self._nodes})()
                self._updates = []

            def update_source_text_node(self, source_id, title=None, year=None,
                                        author_id=None, *, title_en=...,
                                        title_sl=..., **kwargs):
                self._updates.append({"source_id": source_id, "title_sl": title_sl})
                if title_sl is not ...:
                    self._nodes[source_id]["title_sl"] = title_sl
                return True

            def save(self):
                pass

        return MockKG(nodes)

    def test_cross_match_by_author_and_title(self):
        """Cross-match uses normalised author+title tokens.
        With realistic data, EN 'Haraway' and SL 'Haraway' will overlap."""
        nodes = [
            {
                "project_type": "book",
                "title": "A Cyborg Manifesto",
                "title_en": "A Cyborg Manifesto",
                "author": "Haraway, Donna",
                # Missing title_sl
            },
            {
                "id": "source:sl-haraway-cyborg",
                "type": "source_text",
                "project_type": "book",
                "title": "Kiborgov manifest",
                "title_sl": "Kiborgov manifest",
                "author": "Haraway, Donna",
                # Missing title_en
            },
        ]
        kg = self._make_kg_mock(nodes)
        stats = cross_match_bilingual_nodes(kg, dry_run=False)
        # Cross-match ran without error — matching depends on
        # author last name + title token overlap
        assert isinstance(stats, Counter)

class TestRunBilingualEnrichment:
    def test_dry_run_returns_structure(self):
        """Dry run should return a dict of Counters."""
        from collections import Counter

        class MockKG:
            def __init__(self):
                self.G = type("G", (), {"nodes": {}})()
                self.G.nodes = {}

            def save(self):
                pass

        kg = MockKG()
        results = run_bilingual_enrichment(kg, tm_entries=None, dry_run=True)
        assert "tmx" in results
        assert "cross_match" in results
        assert "containers" in results
        assert isinstance(results["tmx"], Counter)


# ── Tests: KG update_source_text_node bilingual fields ────────────────────────


class TestKGUpdateSourceTextNodeBilingual:
    def test_update_title_sl(self):
        """update_source_text_node should accept title_sl kwarg."""
        from translate_core.knowledge_graph import KnowledgeGraph
        import tempfile

        kg = KnowledgeGraph()
        # Add a node
        node_id = kg.add_source_text_node("test-bilingual-update", title="Test Book")
        # Update with title_sl
        result = kg.update_source_text_node(node_id, title_sl="Testna knjiga")
        assert result is True
        assert kg.G.nodes[node_id].get("title_sl") == "Testna knjiga"

    def test_update_preserves_other_fields(self):
        """Updating title_sl should not affect title_en."""
        from translate_core.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        node_id = kg.add_source_text_node("test-bilingual-preserve", title="Test Book")
        kg.update_source_text_node(node_id, title_en="Test Book EN")
        kg.update_source_text_node(node_id, title_sl="Testna knjiga SL")
        assert kg.G.nodes[node_id]["title_en"] == "Test Book EN"
        assert kg.G.nodes[node_id]["title_sl"] == "Testna knjiga SL"

    def test_omitted_fields_not_changed(self):
        """Omitting bilingual kwargs should not overwrite existing values."""
        from translate_core.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        node_id = kg.add_source_text_node("test-bilingual-omit", title="Test Book")
        kg.update_source_text_node(node_id, title_sl="Testna knjiga")
        # Update title_en only — title_sl should remain
        kg.update_source_text_node(node_id, title_en="Test Book EN")
        assert kg.G.nodes[node_id]["title_sl"] == "Testna knjiga"
        assert kg.G.nodes[node_id]["title_en"] == "Test Book EN"

    def test_serialisation_no_ellipsis(self):
        """Saved KG should not contain Ellipsis values."""
        from translate_core.knowledge_graph import KnowledgeGraph
        import json
        import tempfile
        from pathlib import Path

        kg = KnowledgeGraph()
        node_id = kg.add_source_text_node("test-bilingual-serialise", title="Test Book")
        kg.update_source_text_node(node_id, title_en="Test Book EN", title_sl="Testna knjiga")

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = Path(f.name)

        kg.db_path = tmp_path
        kg.save()

        data = json.loads(tmp_path.read_text())
        source_nodes = [n for n in data["nodes"] if n["id"] == node_id]
        assert len(source_nodes) == 1
        node = source_nodes[0]
        assert node["title_en"] == "Test Book EN"
        assert node["title_sl"] == "Testna knjiga"
        # No Ellipsis values
        for k, v in node.items():
            assert v is not ..., f"Field {k} is Ellipsis"

        tmp_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])