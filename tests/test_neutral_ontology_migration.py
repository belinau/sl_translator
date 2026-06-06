"""Phase 4 — KG migration to neutral ontology.

Covers `scripts/migrate_to_neutral_ontology.py` end-to-end against
synthetic tmp_path KG fixtures. Each test exercises one branch of the
migration algorithm (blueprint §5):

- Edge rename: `sl_published_by` → `translation_published_by`
- `translated_by`-to-Belina nodes: SL → translation, EN → orig
- `written_by` only nodes: SL → orig, EN → translation
- Neither edge: routed to `data/migration_review.json` with reason
  `direction_undetermined`; legacy stripped; neutral NOT written
- `slovenian_edition` sub-dict with edge evidence → `translation_edition`
  with `language="sl"`
- `slovenian_edition` sub-dict without edge evidence → captured in review
- `--apply` strips legacy fields
- Second `--apply` is a no-op (idempotency)
- Partial-migration: neutral + legacy → legacy stripped only
- `--dry-run` writes nothing
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "migrate_to_neutral_ontology",
    ROOT / "scripts" / "migrate_to_neutral_ontology.py",
)
assert _spec and _spec.loader
mig_mod = importlib.util.module_from_spec(_spec)
sys.modules["migrate_to_neutral_ontology"] = mig_mod
_spec.loader.exec_module(mig_mod)
migrate = mig_mod.migrate
BELINA_AGENT = mig_mod.BELINA_AGENT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_kg(tmp_path: Path, nodes: list[dict], edges: list[dict]) -> Path:
    path = tmp_path / "kg.json"
    path.write_text(
        json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _load_kg(path: Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["nodes"], data["edges"]


def _node_by_id(nodes: list[dict], nid: str) -> dict:
    for n in nodes:
        if n.get("id") == nid:
            return n
    raise AssertionError(f"node {nid!r} not found in {[n.get('id') for n in nodes]}")


# ---------------------------------------------------------------------------
# A. Edge rename
# ---------------------------------------------------------------------------


def test_edge_rename(tmp_path):
    nodes = [
        {"id": "source:s1", "type": "source_text", "title": "x", "project_type": "book"},
        {"id": "institution:i1", "type": "institution", "name": "I", "kind": "publisher"},
    ]
    edges = [
        {"source": "source:s1", "target": "institution:i1", "relation": "sl_published_by"},
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    review_path = tmp_path / "review.json"

    migrate(kg_path=kg_path, apply=True, review_path=review_path)

    _, new_edges = _load_kg(kg_path)
    assert len(new_edges) == 1
    assert new_edges[0]["relation"] == "translation_published_by"


# ---------------------------------------------------------------------------
# B. translated_by-to-Belina: SL→translation, EN→orig
# ---------------------------------------------------------------------------


def test_node_translator_edge(tmp_path):
    nodes = [
        {
            "id": "source:translated",
            "type": "source_text",
            "title": "x",
            "project_type": "book_translation",
            "title_sl": "Slovenski naslov",
            "title_en": "English Title",
        },
        {
            "id": BELINA_AGENT,
            "type": "agent",
            "name": "Urban Belina",
            "role": "translator",
        },
    ]
    edges = [
        {
            "source": "source:translated",
            "target": BELINA_AGENT,
            "relation": "translated_by",
        },
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    migrate(kg_path=kg_path, apply=True, review_path=tmp_path / "review.json")

    new_nodes, _ = _load_kg(kg_path)
    node = _node_by_id(new_nodes, "source:translated")
    assert node["title_translation"] == "Slovenski naslov"
    assert node["translation_lang"] == "sl"
    assert node["title_orig"] == "English Title"
    assert node["orig_lang"] == "en"
    # Legacy fields are stripped after --apply
    assert "title_sl" not in node
    assert "title_en" not in node


# ---------------------------------------------------------------------------
# C. written_by only: SL→orig, EN→translation
# ---------------------------------------------------------------------------


def test_node_author_edge(tmp_path):
    nodes = [
        {
            "id": "source:authored",
            "type": "source_text",
            "title": "x",
            "project_type": "book",
            "title_sl": "Avtorski naslov",
            "title_en": "Authored Title",
        },
        {
            "id": "agent:author",
            "type": "agent",
            "name": "Jane",
            "role": "author",
        },
    ]
    edges = [
        {"source": "source:authored", "target": "agent:author", "relation": "written_by"},
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    migrate(kg_path=kg_path, apply=True, review_path=tmp_path / "review.json")

    new_nodes, _ = _load_kg(kg_path)
    node = _node_by_id(new_nodes, "source:authored")
    assert node["title_orig"] == "Avtorski naslov"
    assert node["orig_lang"] == "sl"
    assert node["title_translation"] == "Authored Title"
    assert node["translation_lang"] == "en"
    assert "title_sl" not in node
    assert "title_en" not in node


def test_node_author_edge_en_only(tmp_path):
    """`written_by` with only `title_en` → title_orig + orig_lang=en."""
    nodes = [
        {
            "id": "source:authored-en",
            "type": "source_text",
            "title": "x",
            "project_type": "book",
            "title_en": "English Only",
        },
        {"id": "agent:author", "type": "agent", "name": "Jane", "role": "author"},
    ]
    edges = [
        {
            "source": "source:authored-en",
            "target": "agent:author",
            "relation": "written_by",
        },
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    migrate(kg_path=kg_path, apply=True, review_path=tmp_path / "review.json")

    node = _node_by_id(_load_kg(kg_path)[0], "source:authored-en")
    assert node["title_orig"] == "English Only"
    assert node["orig_lang"] == "en"
    assert "title_translation" not in node


# ---------------------------------------------------------------------------
# D. Neither edge: routed to review; legacy stripped, neutral NOT written
# ---------------------------------------------------------------------------


def test_node_no_edge_routes_to_review(tmp_path):
    nodes = [
        {
            "id": "source:orphan",
            "type": "source_text",
            "title": "x",
            "project_type": "cited_work",
            "title_sl": "Slovenski naslov",
            "title_en": "English Title",
            "slovenian_edition": {"publisher": "Maska", "city": "Ljubljana", "year": 2015},
        },
    ]
    edges: list[dict] = []
    kg_path = _write_kg(tmp_path, nodes, edges)
    review_path = tmp_path / "review.json"

    migrate(kg_path=kg_path, apply=True, review_path=review_path)

    new_nodes, _ = _load_kg(kg_path)
    node = _node_by_id(new_nodes, "source:orphan")

    # Legacy fields stripped
    assert "title_sl" not in node
    assert "title_en" not in node
    assert "slovenian_edition" not in node
    # Neutral fields NOT written (no direction evidence)
    assert "title_orig" not in node
    assert "title_translation" not in node
    assert "orig_lang" not in node
    assert "translation_lang" not in node
    # translation_edition also NOT written without direction
    assert "translation_edition" not in node

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert len(review) == 1
    rec = review[0]
    assert rec["node_id"] == "source:orphan"
    assert rec["node_type"] == "source_text"
    assert rec["project_type"] == "cited_work"
    assert rec["title"] == "x"
    assert rec["title_sl_value"] == "Slovenski naslov"
    assert rec["title_en_value"] == "English Title"
    assert rec["slovenian_edition"] == {
        "publisher": "Maska",
        "city": "Ljubljana",
        "year": 2015,
    }
    assert rec["outgoing_relations"] == []
    assert rec["reason"] == "direction_undetermined"


# ---------------------------------------------------------------------------
# E. slovenian_edition migration WITH and WITHOUT edge evidence
# ---------------------------------------------------------------------------


def test_slovenian_edition_with_edge_renames(tmp_path):
    """`slovenian_edition` on a node WITH a translated_by-Belina edge becomes
    `translation_edition` with `language="sl"` injected."""
    nodes = [
        {
            "id": "source:s-with-edition",
            "type": "source_text",
            "title": "x",
            "project_type": "book_translation",
            "title_sl": "SL",
            "title_en": "EN",
            "slovenian_edition": {
                "publisher": "Maska",
                "city": "Ljubljana",
                "year": 2015,
                "translator": "Urban Belina",
            },
        },
        {"id": BELINA_AGENT, "type": "agent", "name": "Urban", "role": "translator"},
    ]
    edges = [
        {
            "source": "source:s-with-edition",
            "target": BELINA_AGENT,
            "relation": "translated_by",
        }
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    migrate(kg_path=kg_path, apply=True, review_path=tmp_path / "review.json")

    node = _node_by_id(_load_kg(kg_path)[0], "source:s-with-edition")
    assert "slovenian_edition" not in node
    assert node["translation_edition"] == {
        "publisher": "Maska",
        "city": "Ljubljana",
        "year": 2015,
        "translator": "Urban Belina",
        "language": "sl",
    }


def test_slovenian_edition_without_edge_routes_to_review(tmp_path):
    """`slovenian_edition` on a node WITHOUT edge evidence is captured in
    the review record AND the legacy field is stripped from the node; no
    `translation_edition` is written (no direction confirmed)."""
    nodes = [
        {
            "id": "source:orphan-edition",
            "type": "source_text",
            "title": "x",
            "project_type": "cited_work",
            "title_sl": "SL",
            "slovenian_edition": {"publisher": "Maska", "year": 2020},
        }
    ]
    kg_path = _write_kg(tmp_path, nodes, [])
    review_path = tmp_path / "review.json"
    migrate(kg_path=kg_path, apply=True, review_path=review_path)

    node = _node_by_id(_load_kg(kg_path)[0], "source:orphan-edition")
    assert "slovenian_edition" not in node
    assert "translation_edition" not in node

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert len(review) == 1
    rec = review[0]
    assert rec["node_id"] == "source:orphan-edition"
    assert rec["slovenian_edition"] == {"publisher": "Maska", "year": 2020}


# ---------------------------------------------------------------------------
# F. Legacy fields stripped under --apply
# ---------------------------------------------------------------------------


def test_legacy_fields_stripped_after_apply(tmp_path):
    """After --apply, no node carries title_en/title_sl/slovenian_edition."""
    nodes = [
        {
            "id": "source:a",
            "type": "source_text",
            "title": "x",
            "project_type": "book_translation",
            "title_sl": "SL",
            "title_en": "EN",
            "slovenian_edition": {"publisher": "P"},
        },
        {"id": BELINA_AGENT, "type": "agent", "name": "U", "role": "translator"},
    ]
    edges = [
        {"source": "source:a", "target": BELINA_AGENT, "relation": "translated_by"},
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    migrate(kg_path=kg_path, apply=True, review_path=tmp_path / "review.json")

    new_nodes, _ = _load_kg(kg_path)
    for node in new_nodes:
        assert "title_en" not in node
        assert "title_sl" not in node
        assert "slovenian_edition" not in node


# ---------------------------------------------------------------------------
# G. Idempotency
# ---------------------------------------------------------------------------


def test_idempotency_second_apply_is_noop(tmp_path):
    nodes = [
        {
            "id": "source:a",
            "type": "source_text",
            "title": "x",
            "project_type": "book_translation",
            "title_sl": "SL",
            "title_en": "EN",
        },
        {"id": BELINA_AGENT, "type": "agent", "name": "U", "role": "translator"},
    ]
    edges = [
        {"source": "source:a", "target": BELINA_AGENT, "relation": "translated_by"},
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    review_path = tmp_path / "review.json"

    migrate(kg_path=kg_path, apply=True, review_path=review_path)
    first_state = kg_path.read_text(encoding="utf-8")

    result = migrate(kg_path=kg_path, apply=True, review_path=review_path)
    second_state = kg_path.read_text(encoding="utf-8")

    assert first_state == second_state, "second apply mutated the KG"
    # Second run finds no legacy fields anywhere
    assert result["nodes_migrated_translator"] == 0
    assert result["nodes_migrated_author"] == 0
    assert result["nodes_already_neutral_legacy_stripped"] == 0


# ---------------------------------------------------------------------------
# H. Partial migration: node with BOTH neutral and legacy → legacy stripped only
# ---------------------------------------------------------------------------


def test_partial_migration_strips_legacy_only(tmp_path):
    nodes = [
        {
            "id": "source:partial",
            "type": "source_text",
            "title": "x",
            "project_type": "book_translation",
            "title_orig": "Existing Orig",
            "title_translation": "Existing Trans",
            "orig_lang": "en",
            "translation_lang": "sl",
            # Legacy lingering — should be stripped, neutral untouched
            "title_sl": "Should be removed",
            "title_en": "Should also be removed",
        },
        {"id": BELINA_AGENT, "type": "agent", "name": "U", "role": "translator"},
    ]
    edges = [
        {
            "source": "source:partial",
            "target": BELINA_AGENT,
            "relation": "translated_by",
        },
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    result = migrate(kg_path=kg_path, apply=True, review_path=tmp_path / "review.json")

    node = _node_by_id(_load_kg(kg_path)[0], "source:partial")
    # Legacy stripped
    assert "title_sl" not in node
    assert "title_en" not in node
    # Neutral preserved (unchanged)
    assert node["title_orig"] == "Existing Orig"
    assert node["title_translation"] == "Existing Trans"
    assert node["orig_lang"] == "en"
    assert node["translation_lang"] == "sl"
    # Bucket
    assert result["nodes_already_neutral_legacy_stripped"] == 1


# ---------------------------------------------------------------------------
# I. Dry-run writes nothing
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write_kg_or_review(tmp_path):
    nodes = [
        {
            "id": "source:s",
            "type": "source_text",
            "title": "x",
            "project_type": "book_translation",
            "title_sl": "SL",
            "title_en": "EN",
        },
        {"id": BELINA_AGENT, "type": "agent", "name": "U", "role": "translator"},
    ]
    edges = [
        {"source": "source:s", "target": BELINA_AGENT, "relation": "translated_by"},
        {"source": "source:s", "target": "institution:x", "relation": "sl_published_by"},
        {"id": "institution:x", "type": "institution", "name": "X", "kind": "publisher"},
    ]
    # Add the institution actually to nodes (not edges)
    nodes.append({"id": "institution:x", "type": "institution", "name": "X", "kind": "publisher"})
    edges = [
        {"source": "source:s", "target": BELINA_AGENT, "relation": "translated_by"},
        {"source": "source:s", "target": "institution:x", "relation": "sl_published_by"},
    ]
    kg_path = _write_kg(tmp_path, nodes, edges)
    review_path = tmp_path / "review.json"

    before = kg_path.read_text(encoding="utf-8")
    result = migrate(kg_path=kg_path, apply=False, review_path=review_path)
    after = kg_path.read_text(encoding="utf-8")

    assert before == after, "dry-run mutated KG file on disk"
    assert not review_path.exists(), "dry-run wrote review queue"
    # Counts are still reported
    assert result["edges_renamed"] == 1
    assert result["nodes_migrated_translator"] == 1
