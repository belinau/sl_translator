"""Phase 4 (TDD red→green) — validator enforces neutral-only KG shape.

Covers the new HARD invariants introduced in blueprint §8: legacy bilingual
node fields (`title_en`, `title_sl`, `slovenian_edition`) and the legacy
edge relation `sl_published_by`. Each must be detected as a HARD-class
violation; neutral-shape nodes and the renamed `translation_published_by`
edge must produce zero violations.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "validate_kg", ROOT / "scripts" / "validate_kg.py"
)
assert _spec and _spec.loader
validate_kg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_kg)
validate = validate_kg.validate
HARD = validate_kg.HARD


def _source(sid, **kw):
    base = {
        "id": sid,
        "type": "source_text",
        "title": "A Real Title",
        "year": 1990,
        "project_type": "book",
    }
    base.update(kw)
    return base


def _agent(aid, **kw):
    base = {
        "id": aid,
        "type": "agent",
        "name": "X",
        "role": "author",
        "dedup_group": "x",
        "alt_spellings": ["X"],
        "all_roles": ["author"],
        "mention_count": 1,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# A. Legacy node fields are HARD violations
# ---------------------------------------------------------------------------


def test_title_en_is_hard_violation():
    """A source_text carrying legacy `title_en` flips `legacy_title_en`."""
    nodes = [_source("source:legacy-en", title_en="Some English title")]
    v = validate(nodes, [])
    assert "legacy_title_en" in v
    assert "source:legacy-en" in v["legacy_title_en"]
    assert "legacy_title_en" in HARD


def test_title_sl_is_hard_violation():
    nodes = [_source("source:legacy-sl", title_sl="Slovenski naslov")]
    v = validate(nodes, [])
    assert "legacy_title_sl" in v
    assert "source:legacy-sl" in v["legacy_title_sl"]
    assert "legacy_title_sl" in HARD


def test_slovenian_edition_is_hard_violation():
    nodes = [
        _source(
            "source:legacy-sl-edition",
            slovenian_edition={"publisher": "Maska", "city": "Ljubljana", "year": 2015},
        )
    ]
    v = validate(nodes, [])
    assert "legacy_slovenian_edition" in v
    assert "source:legacy-sl-edition" in v["legacy_slovenian_edition"]
    assert "legacy_slovenian_edition" in HARD


# ---------------------------------------------------------------------------
# B. Legacy edge relation is a HARD violation
# ---------------------------------------------------------------------------


def test_sl_published_by_edge_is_hard_violation():
    nodes = [
        _source("source:s1", project_type="book_translation"),
        _agent("agent:translator"),
        {
            "id": "institution:maska",
            "type": "institution",
            "name": "Maska",
            "kind": "publisher",
        },
    ]
    edges = [
        {
            "source": "source:s1",
            "target": "agent:translator",
            "relation": "translated_by",
        },
        {
            "source": "source:s1",
            "target": "institution:maska",
            "relation": "sl_published_by",
        },
    ]
    v = validate(nodes, edges)
    assert "legacy_sl_published_by_edge" in v
    assert any(
        "sl_published_by" in entry for entry in v["legacy_sl_published_by_edge"]
    )
    assert "legacy_sl_published_by_edge" in HARD


# ---------------------------------------------------------------------------
# C. Clean neutral shapes produce zero violations
# ---------------------------------------------------------------------------


def test_neutral_node_passes():
    """A source_text carrying ONLY the neutral fields raises no violations."""
    nodes = [
        _source(
            "source:neutral",
            title_orig="Original title",
            title_translation="Translated title",
            orig_lang="en",
            translation_lang="sl",
        ),
    ]
    v = validate(nodes, [])
    for legacy in (
        "legacy_title_en",
        "legacy_title_sl",
        "legacy_slovenian_edition",
        "legacy_sl_published_by_edge",
    ):
        assert legacy not in v, f"unexpected violation {legacy}: {v.get(legacy)}"


def test_translation_published_by_edge_passes():
    """The renamed edge relation is accepted with zero legacy violations."""
    nodes = [
        _source("source:s2", project_type="book_translation"),
        _agent("agent:translator"),
        {
            "id": "institution:maska",
            "type": "institution",
            "name": "Maska",
            "kind": "publisher",
        },
    ]
    edges = [
        {
            "source": "source:s2",
            "target": "agent:translator",
            "relation": "translated_by",
        },
        {
            "source": "source:s2",
            "target": "institution:maska",
            "relation": "translation_published_by",
        },
    ]
    v = validate(nodes, edges)
    assert "legacy_sl_published_by_edge" not in v
