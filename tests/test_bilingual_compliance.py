# tests/test_bilingual_compliance.py
#
# Compliance tests for the `bilingual` audit unit. Each test exercises
# exactly one of the violations called out in
# `data/ontology_audit/bilingual.md`:
#
#   1. cross_match_bilingual_nodes — SL-only orphan removed after merge.
#   2. enrich_containers — orig_lang/translation_lang NOT hard-coded.
#   3. match_citation_against_tm — alt_publishers carries the canonical
#      §2.4.2 `slovenian_edition` shape, no provenance leakage.
#
# All tests are standalone: no real KG (`data/knowledge.db`), no VL model,
# no real TM file. KG interactions go through a MockKG that mirrors the
# narrow surface the patched code actually uses.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.entity_extraction.bibliography_parser import (
    ParsedAuthor,
    ParsedCitation,
)
from translate_core.entity_extraction.bilingual_enrichment_batch import (
    cross_match_bilingual_nodes,
    enrich_containers,
)
from translate_core.entity_extraction.bilingual_tm_matcher import (
    match_citation_against_tm,
)


# ── MockKG ───────────────────────────────────────────────────────────────────


class _FakeNodes:
    """Mimic the read/iter/`in`/`del` surface of networkx `G.nodes`."""

    def __init__(self, mapping):
        self._m = mapping

    def values(self):
        return self._m.values()

    def __contains__(self, key):
        return key in self._m

    def __getitem__(self, key):
        return self._m[key]

    def __iter__(self):
        return iter(self._m)

    def __len__(self):
        return len(self._m)


class MockKG:
    """Minimal in-memory KG mock exposing only the surface the patched
    bilingual code uses: `G.nodes`, `update_source_text_node`, `remove_node`."""

    def __init__(self, nodes):
        self._nodes = {n["id"]: dict(n) for n in nodes}
        self.G = type("G", (), {})()
        self.G.nodes = _FakeNodes(self._nodes)
        self.G.has_node = lambda nid: nid in self._nodes
        self.remove_node_calls: list[str] = []
        self.update_calls: list[dict] = []

    def update_source_text_node(self, source_id, title=None, year=None,
                                author_id=None, **kwargs):
        self.update_calls.append({"source_id": source_id, **kwargs})
        if source_id not in self._nodes:
            return False
        node = self._nodes[source_id]
        # Mirror the real factory's ellipsis-sentinel semantics: ... means
        # "don't change", anything else (including None) is a write.
        for k, v in kwargs.items():
            if v is not ...:
                node[k] = v
        return True

    def remove_node(self, node_id):
        self.remove_node_calls.append(node_id)
        if node_id in self._nodes:
            del self._nodes[node_id]
            return True
        return False


# ── Test 1: cross_match_bilingual_nodes removes SL-only orphan ───────────────


def test_cross_match_removes_sl_only_orphan_after_merge():
    """After merging title_sl onto the EN node, the SL-only node must be
    removed (or otherwise unified) — §4 invariant #4 forbids leaving a
    bare SL-only record alongside the merged bilingual one."""
    en_id = "source:en-cyborg"
    sl_id = "source:sl-cyborg"
    nodes = [
        {
            "id": en_id,
            "type": "source_text",
            "project_type": "book",
            "title": "Cyborg Manifesto",
            "title_en": "Cyborg Manifesto",
            "author": "Haraway, Donna",
        },
        {
            "id": sl_id,
            "type": "source_text",
            "project_type": "book",
            "title": "Cyborg Manifesto",
            "title_sl": "Cyborg Manifesto",
            "author": "Haraway, Donna",
        },
    ]
    kg = MockKG(nodes)

    stats = cross_match_bilingual_nodes(kg, dry_run=False)

    assert stats["cross_matched"] == 1
    # Exactly one source_text record survives, and it carries BOTH titles.
    surviving = [
        n for n in kg.G.nodes.values() if n.get("type") == "source_text"
    ]
    assert len(surviving) == 1, (
        f"expected one merged bilingual node, got {len(surviving)}: "
        f"{[n['id'] for n in surviving]}"
    )
    merged = surviving[0]
    assert merged["id"] == en_id
    assert merged.get("title_en") == "Cyborg Manifesto"
    assert merged.get("title_sl") == "Cyborg Manifesto"

    # The SL-only orphan is gone (short-term repair removes via remove_node).
    assert sl_id not in kg.G.nodes
    assert sl_id in kg.remove_node_calls


# ── Test 2: enrich_containers does NOT hard-code orig_lang='en' ──────────────


def test_enrich_containers_does_not_hardcode_orig_lang_when_unknown():
    """A container whose metadata says `language='de'` (German original)
    must NOT be relabelled `orig_lang='en'`. When derivation isn't
    possible, both language fields stay unset rather than wrong."""
    node_id = "source:de-container"
    nodes = [
        {
            "id": node_id,
            "type": "source_text",
            "project_type": "book_translation",
            "title": "Die ursprüngliche Akkumulation",   # neither EN nor SL surface
            "title_en": "The Original Accumulation",
            "title_sl": "Prvotna akumulacija",
            "language": "de",
        }
    ]
    kg = MockKG(nodes)

    enrich_containers(kg, dry_run=False)

    merged = kg._nodes[node_id]
    # Hard-coded 'en'/'sl' would be a wrong-value write. Audit forbids both.
    assert merged.get("orig_lang") != "en", (
        "orig_lang was hard-coded to 'en' for a non-English original"
    )
    assert merged.get("translation_lang") != "sl" or (
        merged.get("orig_lang") not in (None, "en")
    ), "translation_lang hard-coded without a derivable orig_lang"
    # In this fixture derivation is impossible → fields stay unset.
    assert "orig_lang" not in merged
    assert "translation_lang" not in merged


def test_enrich_containers_derives_orig_lang_from_language_field():
    """Positive path: when `language='en'` is present on the container,
    orig_lang/translation_lang ARE derived (not skipped)."""
    node_id = "source:en-container"
    nodes = [
        {
            "id": node_id,
            "type": "source_text",
            "project_type": "book_translation",
            "title": "The Original",
            "title_en": "The Original",
            "title_sl": "Izvirnik",
            "language": "en",
        }
    ]
    kg = MockKG(nodes)
    enrich_containers(kg, dry_run=False)
    merged = kg._nodes[node_id]
    assert merged.get("orig_lang") == "en"
    assert merged.get("translation_lang") == "sl"


# ── Test 3: match_citation_against_tm alt_publishers canonical shape ─────────


def test_alt_publishers_dict_matches_canonical_slovenian_edition_shape():
    """Per §2.4.2, an alt-publisher entry surfaced from the TM must use
    the canonical `slovenian_edition` shape — exactly the keys
    `{publisher, city, year, translator}`. Provenance leakage
    (`source_origin`, `source_global_idx`) is forbidden in this dict."""
    citation = ParsedCitation(
        raw="Foucault, Discipline and Punish, 1977, p. 145, New York: Pantheon",
        authors=[ParsedAuthor(surname="Foucault", given="Michel")],
        year=1977,
        title="Discipline and Punish",
        pages="145",
        publisher="Pantheon",
        place="New York",
        citation_type="book",
    )
    # Two separate entries so the SL match (with the alt SL publisher) is
    # NOT deduped against the stronger EN match — alt-pub mining must run on
    # the SL-side matched_form for the canonical-shape assertion to fire.
    tm_entries = [
        {
            "origin": "test-tm.tmx",
            "source": (
                "Michel Foucault, Discipline and Punish, 1977, p. 145, "
                "New York: Pantheon"
            ),
            "target": "",
        },
        {
            "origin": "test-tm.tmx",
            "source": "",
            "target": (
                "Michel Foucault, Nadzorovanje in kaznovanje, str. 145, "
                "Ljubljana: Študentska založba, 1984, prev. Drago Bajt"
            ),
        },
    ]

    result = match_citation_against_tm(citation, tm_entries)

    assert result.alt_publishers, "expected at least one alt-publisher entry"
    alt = result.alt_publishers[0]
    assert set(alt.keys()) == {"publisher", "city", "year", "translator"}, (
        f"alt_publishers entry has wrong keys: {sorted(alt.keys())}"
    )
    # Provenance keys must be absent — §2.4.2 forbids ad-hoc fields.
    assert "source_origin" not in alt
    assert "source_global_idx" not in alt
    # Sanity: the SL publisher was actually picked up.
    assert alt["publisher"] and "tudentska" in alt["publisher"].lower().replace("š", "s")
    assert alt["city"] == "Ljubljana"
