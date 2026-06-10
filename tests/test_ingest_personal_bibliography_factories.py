# tests/test_ingest_personal_bibliography_factories.py
#
# Ontology repair test for scripts/ingest_personal_bibliography.py.
# Verifies that ingest_bibliography wires written_by / translated_by /
# published_by exclusively through KnowledgeGraph factory methods
# (link_written_by, link_translated_by, link_published_by) and never
# calls kg.G.add_edge directly from the ingest script.
#
# Uses an in-memory KnowledgeGraph (db_path points at a non-existent file
# so _load is a no-op) and a monkey-patched parse_cobiss_file that yields
# a single hand-built CobissEntry. Never reads or writes data/knowledge.db.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from translate_core.cobiss_parser import CobissEntry, CobissAgent
from translate_core.knowledge_graph import KnowledgeGraph
import scripts.ingest_personal_bibliography as ing


INGEST_SCRIPT_PATH = str(Path(ing.__file__).resolve())


def _fixture_entry() -> CobissEntry:
    """One container entry: one foreign author + a publisher. The curator is
    NOT listed among the agents — classify_entry will treat the entry as
    a book_translation with an implicit translator role (so exactly one
    written_by edge fires, alongside one translated_by and one
    published_by)."""
    return CobissEntry(
        entry_number=1,
        raw_text="",
        agents=[CobissAgent(last_name="DOE", first_name="Jane", roles=["author"])],
        title="Test Container Title",
        publisher="Test Publisher",
        year=2024,
        isbn=["978-1-23-456789-0"],
    )


def _build_kg(tmp_path: Path) -> KnowledgeGraph:
    """In-memory KG anchored at a non-existent path (so _load is a no-op
    and nothing is read from disk)."""
    return KnowledgeGraph(db_path=tmp_path / "does_not_exist.db")


def test_ingest_uses_only_factory_methods(monkeypatch, tmp_path):
    """Drive ingest_bibliography with a one-entry fixture and assert:
      * kg.G.add_edge is never called from ingest_personal_bibliography.py
        (every edge write goes through a KnowledgeGraph factory method,
        whose call frame lives in knowledge_graph.py instead).
      * Exactly one written_by, one translated_by, one published_by edge
        ends up on the graph.
    """
    kg = _build_kg(tmp_path)

    # Wrap kg.G.add_edge so any direct call originating from the ingest
    # script raises immediately. Factory methods on KnowledgeGraph call
    # self.G.add_edge from knowledge_graph.py, which is allowed.
    raw_calls_from_ingest: list[tuple[str, int]] = []
    real_add_edge = kg.G.add_edge

    def guarded_add_edge(u, v, **kwargs):
        caller = sys._getframe(1)
        caller_file = caller.f_code.co_filename
        if Path(caller_file).resolve() == Path(INGEST_SCRIPT_PATH):
            raw_calls_from_ingest.append((caller_file, caller.f_lineno))
            raise AssertionError(
                f"Direct kg.G.add_edge from ingest script "
                f"{caller_file}:{caller.f_lineno} (relation="
                f"{kwargs.get('relation')!r})"
            )
        return real_add_edge(u, v, **kwargs)

    kg.G.add_edge = guarded_add_edge  # type: ignore[assignment]

    # Patch the ingest module: hand it our prebuilt KG and a single
    # entry instead of parsing a real COBISS file.
    monkeypatch.setattr(ing, "KnowledgeGraph", lambda db_path: kg)
    monkeypatch.setattr(ing, "parse_cobiss_file", lambda _path: [_fixture_entry()])

    report = ing.ingest_bibliography(
        cobiss_path=tmp_path / "fake.txt",
        kg_path=tmp_path / "does_not_exist.db",
        dry_run=True,
    )

    assert raw_calls_from_ingest == [], (
        f"ingest script wrote edges directly: {raw_calls_from_ingest}"
    )

    edges = list(kg.G.edges(data=True))
    by_relation: dict[str, list[tuple[str, str]]] = {}
    for u, v, data in edges:
        by_relation.setdefault(data.get("relation"), []).append((u, v))

    # Repair note for lines 246–248: written_by via link_written_by.
    assert len(by_relation.get("written_by", [])) == 1, by_relation
    # Repair note for lines 252–254: translated_by via link_translated_by.
    assert len(by_relation.get("translated_by", [])) == 1, by_relation
    # Repair note for lines 268–270: published_by via link_published_by.
    assert len(by_relation.get("published_by", [])) == 1, by_relation

    # Sanity: the report counts all three new edges.
    assert report["edges_created"] >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
